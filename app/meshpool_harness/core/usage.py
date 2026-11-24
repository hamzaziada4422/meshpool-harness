"""One usage ledger for every model call in the process tree.

Every call reports the provider's own token numbers here (``record``). Each record goes to:

- ``MESHPOOL_HOME/usage/calls-YYYY-MM.jsonl``: the global ledger the Usage page reads;
- ``MESHPOOL_HOME/usage/tasks/<task>.jsonl`` for the current task and every ancestor task: what budgets read.

Nothing blocks on the per-call path. ``record`` builds the record, adds it to the in-process totals of the task chain
and puts it on a queue. One daemon thread appends the queue to the files every 0.5 s (and at exit, and on
``flush()``), one append per file under ``fsio.file_lock``. ``Task.spent()``, ``totals()`` and ``poll()`` merge what
this process recorded (memory, exact at once) with what the files hold (other processes, visible after their next
flush), de-duplicated by record id. Prices never come from the network on the call path: a background thread fetches
the endpoint's ``/models`` prices (``prefetch_prices``); until it lands the override or the table is used.

Settings (environment first, then meshpool.config):

- ``MH_BUDGET_USD``: spending limit per task, 0 = none.
- ``MH_PRICES``: JSON ``{"model or glob": {"in": 1, "out": 2, "cached": 0.1, "cache_write": 1.25}}`` (USD per 1M).
- ``MH_PRICE_IN`` / ``MH_PRICE_OUT`` / ``MH_PRICE_CACHED``: one global price for every model (needs IN and OUT).
- ``MH_BUDGET_PRICES``: ``"in,cached,out"`` USD per 1M; when set, budgets (``budget_cost``) use these prices for every
  model (the override the engine loop always had).
- ``MH_ENDPOINT_PRICES=0``: never fetch ``/models`` prices.  ``MH_USAGE_LEDGER=0``: write no files.
"""
from __future__ import annotations

import atexit
import contextlib
import contextvars
import fnmatch
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from meshpool_harness import config as C
from meshpool_harness import fsio

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_ID_OK = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_WARNED: set[str] = set()


def _warn(what: str, e: BaseException) -> None:
    if what in _WARNED:
        return
    _WARNED.add(what)
    with contextlib.suppress(Exception):
        print(f"[meshpool usage] {what}: {e}", file=sys.stderr)


# ---------------------------------------------------------------- settings

_CFG: dict[str, Any] = {"key": None, "val": {}, "at": 0.0}


def _cfg() -> dict:
    """meshpool.config, re-read only when the file changed (checked at most once a second)."""
    now = time.monotonic()
    if now - _CFG["at"] < 1.0:
        return _CFG["val"]
    _CFG["at"] = now
    try:
        p = C.config_path()
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        _CFG["key"], _CFG["val"] = None, {}
        return {}
    if _CFG["key"] != key:
        try:
            _CFG["val"] = C.read()
        except Exception:
            _CFG["val"] = {}
        _CFG["key"] = key
    return _CFG["val"]


def _setting(k: str) -> str:
    v = os.environ.get(k)
    if v is not None and v.strip():
        return v.strip()
    return str(_cfg().get(k) or "").strip()


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x >= 0 else None


_HOME_CACHE: list = [None, None]


def _home() -> Path:
    env = os.environ.get("MESHPOOL_HOME")
    if _HOME_CACHE[0] != env or _HOME_CACHE[1] is None:
        _HOME_CACHE[0], _HOME_CACHE[1] = env, C.home()
    return _HOME_CACHE[1]


def _usage_dir(home: Path | None = None) -> Path:
    return (home or _home()) / "usage"


# ---------------------------------------------------------------- errors


class BudgetExceeded(Exception):
    """The task reached its spending limit. Not a RuntimeError, so retry loops never swallow it."""

    def __init__(self, task: str, budget: float, spent: float):
        self.task = task
        self.budget = float(budget)
        self.spent = float(spent)
        super().__init__(task, budget, spent)

    def __str__(self) -> str:
        return (f"Spending limit reached: this task has used ${self.spent:.2f} of its ${self.budget:.2f} limit "
                f"(Settings, Spending limit per task).")

    def __reduce__(self):
        return (BudgetExceeded, (self.task, self.budget, self.spent))


# ---------------------------------------------------------------- tokens


def _n(v: Any) -> int:
    if isinstance(v, bool):
        return 0
    try:
        x = int(float(v))
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, x)


def _d(raw: dict, k: str) -> dict:
    v = raw.get(k)
    return v if isinstance(v, dict) else {}


def _first(*vals: Any) -> int:
    for v in vals:
        n = _n(v)
        if n:
            return n
    return 0


def _present(raw: dict, *keys: str) -> bool:
    return any(raw.get(k) is not None for k in keys)


def _sum_or_int(v: Any) -> int:
    if isinstance(v, dict):
        return sum(_n(x) for x in v.values())
    return _n(v)


def normalize(raw: dict | None) -> dict:
    """Provider ``usage`` -> {in, cached, cache_write, out, reasoning, reported}. ``in`` includes cached and
    cache-write tokens; reasoning tokens are already inside ``out``. Also reads pi (input/output/cacheRead/cacheWrite),
    ACP (camelCase; ``inputTokens`` excludes the cache reads and writes) and Codex exec (``input_tokens`` includes
    ``cached_input_tokens``; ``reasoning_output_tokens`` are inside ``output_tokens``)."""
    z = {"in": 0, "cached": 0, "cache_write": 0, "out": 0, "reasoning": 0, "reported": False}
    if not isinstance(raw, dict) or not raw:
        return z
    in_ = cached = cw = out = reasoning = 0
    if _present(raw, "prompt_tokens", "completion_tokens"):                      # chat completions, DeepSeek
        ptd, ctd = _d(raw, "prompt_tokens_details"), _d(raw, "completion_tokens_details")
        in_, out = _n(raw.get("prompt_tokens")), _n(raw.get("completion_tokens"))
        cached = _first(ptd.get("cached_tokens"), raw.get("prompt_cache_hit_tokens"),
                        raw.get("cache_read_input_tokens"), raw.get("cached_tokens"))
        cw = _first(ptd.get("cache_write_tokens"), raw.get("cache_creation_input_tokens"),
                    _sum_or_int(raw.get("cache_creation")), raw.get("cache_write"), raw.get("cache_write_tokens"))
        reasoning = _first(ctd.get("reasoning_tokens"), raw.get("reasoning_tokens"))
        if cached + cw > in_:                       # a converted shape that left the cache tokens out of the prompt
            in_ += cached + cw
    elif _present(raw, "cached_input_tokens", "reasoning_output_tokens"):         # Codex exec turn.completed
        in_, cached, out = _n(raw.get("input_tokens")), _n(raw.get("cached_input_tokens")), _n(raw.get("output_tokens"))
        reasoning = _n(raw.get("reasoning_output_tokens"))    # input includes the cached part; output the reasoning
        if cached > in_:
            in_ += cached
    elif _present(raw, "inputTokens", "outputTokens"):                            # ACP: inputTokens excludes the cache
        cached, cw, out = _n(raw.get("cachedReadTokens")), _n(raw.get("cachedWriteTokens")), _n(raw.get("outputTokens"))
        in_ = _n(raw.get("inputTokens")) + cached + cw
        reasoning = _n(raw.get("thoughtTokens"))
    elif _present(raw, "input_tokens", "output_tokens"):                          # Responses API, Anthropic
        out = _n(raw.get("output_tokens"))
        itd, otd = _d(raw, "input_tokens_details"), _d(raw, "output_tokens_details")
        reasoning = _first(otd.get("reasoning_tokens"), raw.get("reasoning_tokens"))
        if "input_tokens_details" in raw:                                         # Responses: input includes cached
            in_ = _n(raw.get("input_tokens"))
            cached = _n(itd.get("cached_tokens"))
            cw = _first(raw.get("cache_creation_input_tokens"), itd.get("cache_write_tokens"))
            if cached + cw > in_:
                in_ += cached + cw
        else:                                                                     # Anthropic: input excludes the cache
            cached = _n(raw.get("cache_read_input_tokens"))
            cw = _first(raw.get("cache_creation_input_tokens"), _sum_or_int(raw.get("cache_creation")))
            in_ = _n(raw.get("input_tokens")) + cached + cw
    elif _present(raw, "input", "output", "cacheRead", "cacheWrite"):             # pi: input excludes the cache
        cached, cw, out = _n(raw.get("cacheRead")), _n(raw.get("cacheWrite")), _n(raw.get("output"))
        in_ = _n(raw.get("input")) + cached + cw
    else:
        return z
    return {"in": in_, "cached": min(cached, in_), "cache_write": min(cw, max(0, in_ - min(cached, in_))),
            "out": out, "reasoning": min(reasoning, out), "reported": True}


def estimate(request_chars: int, reply_chars: int) -> dict:
    """Tokens from characters (/3, deliberately high). Flagged ``est``; a call is never worth zero."""
    rq, rp = max(0, int(request_chars or 0)), max(0, int(reply_chars or 0))
    return {"in": max(1, math.ceil(rq / 3)), "cached": 0, "cache_write": 0, "out": math.ceil(rp / 3),
            "reasoning": 0, "reported": False, "est": True}


# ---------------------------------------------------------------- prices

_TABLE_FILE = C.APP / "defaults" / "prices.json"
_TABLE_CACHE: dict[str, Any] = {}
_PROVIDER_HEADS = ("chatgpt", "key", "claude", "anthropic", "google", "gemini", "openai")


def _bare(model: str) -> str:
    m = (model or "").strip()
    head, sep, rest = m.partition(":")
    if sep and head.lower() in _PROVIDER_HEADS and rest:
        m = rest
    return m


def _candidates(model: str) -> list[str]:
    m = _bare(model).lower()
    out = [m]
    if "/" in m:
        out.append(m.rsplit("/", 1)[1])
    return [x for x in out if x]


def _table() -> dict:
    p = str(_TABLE_FILE)
    t = _TABLE_CACHE.get(p)
    if t is None:
        try:
            doc = json.loads(Path(p).read_text(encoding="utf-8-sig"))
            models = doc.get("models") if isinstance(doc, dict) else {}
            t = {str(k).lower(): v for k, v in (models or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            t = {}
        _TABLE_CACHE[p] = t
    return t


def _host_table(host: str) -> dict:
    """The table's per-host prices (``hosts``: what a gateway really bills), lower-cased by model."""
    key = ("hosts", str(_TABLE_FILE))
    hosts = _TABLE_CACHE.get(key)
    if hosts is None:
        try:
            doc = json.loads(Path(_TABLE_FILE).read_text(encoding="utf-8-sig"))
            raw = doc.get("hosts") if isinstance(doc, dict) else {}
            hosts = {str(h).lower(): {str(k).lower(): v for k, v in (m or {}).items() if isinstance(v, dict)}
                     for h, m in (raw or {}).items() if isinstance(m, dict)}
        except (OSError, ValueError):
            hosts = {}
        _TABLE_CACHE[key] = hosts
    return hosts.get(host.lower(), {})


def _mk(src: str, pin: Any, pout: Any, pc: Any = None, pw: Any = None) -> dict | None:
    i, o = _num(pin), _num(pout)
    if i is None or o is None:
        return None
    c, w = _num(pc), _num(pw)
    return {"in": i, "cached": i / 10 if c is None else c, "cache_write": i * 1.25 if w is None else w,
            "out": o, "source": src}


def _match(mapping: dict, model: str) -> Any:
    cands = _candidates(model)
    for c in cands:
        if c in mapping:
            return mapping[c]
    best, blen = None, -1
    for pat, v in mapping.items():
        if any(ch in pat for ch in "*?[") and len(pat) > blen and any(fnmatch.fnmatchcase(c, pat) for c in cands):
            best, blen = v, len(pat)
    return best


_PRICES_PARSED: dict[str, dict] = {}


def _override_map() -> dict:
    raw = _setting("MH_PRICES")
    if not raw:
        return {}
    got = _PRICES_PARSED.get(raw)
    if got is None:
        got = {}
        try:
            doc = json.loads(raw)
            for k, v in (doc.items() if isinstance(doc, dict) else ()):
                if isinstance(v, dict) and _mk("setting", v.get("in"), v.get("out")):
                    got[str(k).lower()] = v
        except ValueError:
            pass
        _PRICES_PARSED[raw] = got
    return got


def _override(model: str) -> dict | None:
    v = _match(_override_map(), model)
    if v is not None:
        return _mk("setting", v.get("in"), v.get("out"), v.get("cached"), v.get("cache_write"))
    pin, pout = _setting("MH_PRICE_IN"), _setting("MH_PRICE_OUT")
    if _num(pin) is not None and _num(pout) is not None:
        return _mk("setting", pin, pout, _setting("MH_PRICE_CACHED") or None)
    return None


# Endpoint prices: fetched in the background, never on the call path.
_EP_LOCK = threading.Lock()
_EP_MEM: dict[tuple[str, str], dict] = {}        # (home, base) -> {"prices": {...}, "fresh_until": ts}
_EP_NEXT: dict[tuple[str, str], float] = {}      # (home, base) -> earliest time of the next attempt
_EP_THREADS: dict[tuple[str, str], threading.Thread] = {}


def _endpoint_enabled() -> bool:
    return os.environ.get("MH_ENDPOINT_PRICES", "1").strip() != "0"


def _per_million(pr: dict) -> dict | None:
    if "prompt" in pr or "completion" in pr:                    # OpenRouter style: USD per token
        f = 1e6
        vals = [pr.get("prompt"), pr.get("completion"), pr.get("input_cache_read"), pr.get("input_cache_write")]
    else:                                                       # per 1M
        f = 1.0
        vals = [pr.get("in", pr.get("input")), pr.get("out", pr.get("output")),
                pr.get("cached", pr.get("cache_read", pr.get("input_cache_read"))),
                pr.get("cache_write", pr.get("input_cache_write"))]
    nums = [None if _num(v) is None else round(_num(v) * f, 6) for v in vals]
    if nums[0] is None or nums[1] is None:
        return None
    return {"in": nums[0], "out": nums[1], "cached": nums[2], "cache_write": nums[3]}


def _parse_models(doc: Any) -> dict:
    items = doc.get("data") if isinstance(doc, dict) else doc
    out: dict[str, dict] = {}
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        mid = str(it.get("id") or it.get("name") or "").lower()
        pr = it.get("pricing") if isinstance(it.get("pricing"), dict) else it.get("price")
        p = _per_million(pr) if isinstance(pr, dict) and mid else None
        if p:
            out[mid] = p
            out.setdefault(mid.rsplit("/", 1)[-1], p)
    return out


def _ep_file(home: Path, base: str) -> Path:
    return _usage_dir(home) / f"endpoint-prices-{hashlib.sha256(base.encode()).hexdigest()[:16]}.json"


def _fetch_endpoint_prices(base: str, home: Path | None = None) -> dict:
    """Blocking: disk cache, else GET {base}/models (3 s). Stores the result in memory and on disk. Never raises."""
    base = (base or "").rstrip("/")
    home = home or _home()
    mk = (str(home), base)
    now = time.time()
    prices: dict = {}
    ttl = 600.0
    try:
        path = _ep_file(home, base)
        try:
            disk = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(disk, dict) and disk.get("until", 0) > now and isinstance(disk.get("prices"), dict):
                with _EP_LOCK:
                    _EP_MEM[mk] = {"prices": disk["prices"], "fresh_until": disk["until"]}
                    _EP_NEXT[mk] = disk["until"]
                return disk["prices"]
        except (OSError, ValueError):
            pass
        prices, ok = _http_prices(base), True
        ttl = 86400.0
    except Exception:
        prices, ok = {}, False
    if prices is None:
        prices, ok, ttl = {}, False, 600.0
    until = now + ttl
    with _EP_LOCK:
        _EP_MEM[mk] = {"prices": prices, "fresh_until": until}
        _EP_NEXT[mk] = until
    with contextlib.suppress(Exception):
        fsio.atomic_write_json(_ep_file(home, base), {"until": until, "ok": ok, "prices": prices}, fsync=False)
    return prices


def _http_prices(base: str) -> dict | None:
    """The parsed price map, or None when the fetch failed."""
    import urllib.parse
    import urllib.request
    try:
        u = urllib.parse.urlparse(base)
        if u.scheme not in ("http", "https") or not u.hostname:
            return None
        headers = {"Accept": "application/json", "User-Agent": "meshpool"}
        try:                                                    # send the key only to the endpoint it belongs to
            from meshpool_harness import providers as P
            k = P.key()
            if k and P.host(P.key_base()) == u.hostname.lower():
                headers["Authorization"] = f"Bearer {k}"
        except Exception:
            pass
        req = urllib.request.Request(base + "/models", headers=headers)
        from meshpool_harness.core import http as H    # redirects refused: a 302 elsewhere never gets the key
        with H.open(req, timeout=3) as r:
            body = r.read(8 * 1024 * 1024)
        return _parse_models(json.loads(body.decode("utf-8", "replace")))
    except Exception:
        return None


def prefetch_prices(base: str) -> threading.Thread | None:
    """Start the background fetch of the endpoint's prices unless one ran lately (24 h, 10 min after a failure).
    Returns the thread, or None when nothing was started."""
    base = (base or "").rstrip("/")
    if not base or not _endpoint_enabled():
        return None
    home = _home()
    mk = (str(home), base)
    with _EP_LOCK:
        if time.time() < _EP_NEXT.get(mk, 0.0):
            return None
        _EP_NEXT[mk] = time.time() + 600.0                       # in flight: no second start meanwhile
        t = threading.Thread(target=_fetch_endpoint_prices, args=(base, home), name="usage-prices", daemon=True)
        _EP_THREADS[mk] = t
    t.start()
    return t


def _endpoint_price(model: str, base: str) -> dict | None:
    if not base or not _endpoint_enabled():
        return None
    base = base.rstrip("/")
    ent = _EP_MEM.get((str(_home()), base))
    if ent is None or time.time() >= ent["fresh_until"]:
        prefetch_prices(base)
    if ent is None:
        return None
    v = _match(ent["prices"], model)
    return _mk("endpoint", v.get("in"), v.get("out"), v.get("cached"), v.get("cache_write")) if v else None


def price_for(model: str, base: str = "") -> dict:
    """USD per 1M tokens {in, cached, cache_write, out, source}. Never blocks on the network."""
    p = _override(model) or _endpoint_price(model, base)
    if p is None:
        host = ""
        if base:
            from urllib.parse import urlparse
            host = urlparse(base).hostname or ""
        v = _match(_host_table(host), model) if host else None     # a gateway's own prices come before list prices
        v = v or _match(_table(), model)
        p = _mk("table", v.get("in"), v.get("out"), v.get("cached"), v.get("cache_write")) if v else None
    return p or {"in": None, "cached": None, "cache_write": None, "out": None, "source": "unknown"}


def _formula(p: dict, norm: dict) -> float:
    pin, pout = p["in"], p["out"]
    pc = p.get("cached")
    pw = p.get("cache_write")
    pc = pin / 10 if pc is None else pc
    pw = pin * 1.25 if pw is None else pw
    i, c, w, o = _n(norm.get("in")), _n(norm.get("cached")), _n(norm.get("cache_write")), _n(norm.get("out"))
    fresh = max(0, i - c - w)
    return (fresh * pin + c * pc + w * pw + o * pout) / 1e6


def cost_of(model: str, norm: dict, base: str = "") -> tuple[float | None, str]:
    """(USD, price source). None when the price is unknown. Reasoning tokens are inside ``out``."""
    p = price_for(model, base)
    if p["in"] is None or p["out"] is None:
        return None, p["source"]
    return _formula(p, norm), p["source"]


_FALLBACK_TOP = (8.0, 40.0)


def _top_prices() -> dict:
    t = _table()
    ins = [_num(v.get("in")) for v in t.values()]
    outs = [_num(v.get("out")) for v in t.values()]
    ins, outs = [x for x in ins if x is not None], [x for x in outs if x is not None]
    pin = max(ins) if ins else _FALLBACK_TOP[0]
    pout = max(outs) if outs else _FALLBACK_TOP[1]
    return {"in": pin, "cached": pin / 10, "cache_write": pin * 1.25, "out": pout, "source": "table"}


def _budget_from(cost: float | None, norm: dict) -> float:
    bp = [x.strip() for x in _setting("MH_BUDGET_PRICES").split(",")]
    if len(bp) == 3 and all(_num(x) is not None for x in bp):
        pin = _num(bp[0])
        return _formula({"in": pin, "cached": _num(bp[1]), "cache_write": pin * 1.25, "out": _num(bp[2])}, norm)
    return cost if cost is not None else _formula(_top_prices(), norm)


def budget_cost(model: str, norm: dict, base: str = "") -> float:
    """What a call counts against a budget: its cost, or at the most expensive table price when unknown
    (MH_BUDGET_PRICES overrides both)."""
    return _budget_from(cost_of(model, norm, base)[0], norm)


# ---------------------------------------------------------------- tasks

_TASKS: dict[tuple[str, str], "Task"] = {}
_TASKS_LOCK = threading.Lock()
_CUR: contextvars.ContextVar = contextvars.ContextVar("meshpool_usage_task", default=None)


def _safe(s: str, n: int = 60) -> str:
    return _SAFE.sub("_", s or "")[:n]


def _empty_bucket() -> dict:
    return {"in": 0, "cached": 0, "cache_write": 0, "out": 0, "reasoning": 0, "calls": 0, "cost": 0.0,
            "unpriced_calls": 0, "unreported_calls": 0}


def _add(b: dict, r: dict) -> None:
    for k in ("in", "cached", "cache_write", "out", "reasoning"):
        b[k] += _n(r.get(k))
    b["calls"] += 1
    if r.get("cost") is None:
        b["unpriced_calls"] += 1
    else:
        b["cost"] += float(r["cost"])
    if not r.get("reported"):
        b["unreported_calls"] += 1


def _summarize(records, groups: bool) -> dict:
    t = _empty_bucket()
    t.update(budget_cost=0.0, by_model={}, by_source={})
    if groups:
        t.update(by_chat={}, by_day={})
    for r in records:
        if not isinstance(r, dict):
            continue
        _add(t, r)
        t["budget_cost"] += _num(r.get("budget_cost")) or 0.0
        _add(t["by_model"].setdefault(str(r.get("model") or "?"), _empty_bucket()), r)
        _add(t["by_source"].setdefault(str(r.get("source") or "?"), _empty_bucket()), r)
        if groups:
            _add(t["by_chat"].setdefault(str(r.get("chat") or ""), _empty_bucket()), r)
            day = time.strftime("%Y-%m-%d", time.localtime(float(r.get("ts") or 0)))
            _add(t["by_day"].setdefault(day, _empty_bucket()), r)
    return t


def summarize(records) -> dict:
    """Totals plus by_model, by_source, by_chat and by_day (local time)."""
    return _summarize(records, True)


class Task:
    def __init__(self, id: str, chat: str = "", parent: str | None = None, budget: float = 0.0, label: str = "",
                 started: float = 0.0, home: Path | None = None):
        self.id, self.chat, self.parent = id, chat, parent
        self.budget = float(budget or 0.0)
        self.label, self.started = label, started
        self.home = Path(home) if home else _home()
        self.path = self.home / "usage" / "tasks" / f"{id}.jsonl"
        self.spath = str(self.path)
        self._lk = threading.RLock()
        self._off = 0
        self._seen: set[str] = set()
        self._all: list[dict] = []
        self._new: list[dict] = []
        self._spent = 0.0
        self._chain: list[Task] | None = None
        self._parent_obj: Task | None = None

    def __repr__(self) -> str:
        return f"Task({self.id!r}, budget={self.budget})"

    def chain(self) -> list["Task"]:
        """This task, then its parent, grandparent... (cycle-safe)."""
        if self._chain is not None:
            return self._chain
        out: list[Task] = [self]
        ids = {self.id}
        pid, complete = self.parent, True
        cur = self
        while pid and len(out) < 16:
            nxt = cur._parent_obj if cur._parent_obj is not None and cur._parent_obj.id == pid else _load_task(pid, self.home)
            if nxt is None or nxt.id in ids:
                complete = nxt is not None
                break
            out.append(nxt)
            ids.add(nxt.id)
            cur, pid = nxt, nxt.parent
        if complete:
            self._chain = out
        return out

    # records ---------------------------------------------------------
    def _ingest(self, r: dict) -> None:
        rid = r.get("id")
        if rid in self._seen:
            return
        self._seen.add(rid)
        self._all.append(r)
        self._new.append(r)
        self._spent += _num(r.get("budget_cost")) or 0.0

    def _add_local(self, r: dict) -> None:
        with self._lk:
            self._ingest(r)

    def _refresh(self) -> None:
        """Fold in lines other processes (or an earlier run) appended to the task file."""
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size == self._off:
            return
        if size < self._off:                         # file replaced or pruned: read it again, ids de-duplicate
            self._off = 0
        try:
            with open(self.path, "rb") as f:
                f.seek(self._off)
                data = f.read()
        except OSError:
            return
        end = data.rfind(b"\n")
        if end < 0:
            return
        self._off += end + 1
        for line in data[:end].split(b"\n"):
            if not line.strip():
                continue
            try:
                r = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(r, dict):
                self._ingest(r)

    def spent(self) -> float:
        """Budget cost of every call of this task and its descendants (memory merged with the file)."""
        with self._lk:
            self._refresh()
            return self._spent

    def records(self) -> list[dict]:
        with self._lk:
            self._refresh()
            return list(self._all)

    def totals(self) -> dict:
        return _summarize(self.records(), False)

    def poll(self) -> list[dict]:
        """Records not returned by an earlier poll() of this Task object."""
        with self._lk:
            self._refresh()
            out, self._new = self._new, []
            return out


def _load_task(tid: str, home: Path) -> Task | None:
    if not _ID_OK.match(tid or ""):
        return None
    key = (str(home), tid)
    with _TASKS_LOCK:
        t = _TASKS.get(key)
    if t is not None:
        return t
    try:
        meta = fsio.read_json(home / "usage" / "tasks" / f"{tid}.json", None)
    except fsio.CorruptFile:
        return None
    if not isinstance(meta, dict):
        return None
    t = Task(tid, str(meta.get("chat") or ""), meta.get("parent") or None, _num(meta.get("budget")) or 0.0,
             str(meta.get("label") or ""), float(meta.get("started") or 0), home)
    with _TASKS_LOCK:
        return _TASKS.setdefault(key, t)


def get_task(id: str) -> Task | None:
    return _load_task(id, _home())


_PRUNED = {"at": 0.0}


def _prune(home: Path) -> None:
    now = time.time()
    if now - _PRUNED["at"] < 3600:
        return
    _PRUNED["at"] = now
    cutoff = now - 14 * 86400
    for d in (home / "usage" / "tasks", home / "usage" / "current"):
        try:
            for p in d.iterdir():
                with contextlib.suppress(OSError):
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink()
        except OSError:
            pass


def start_task(chat: str = "", budget_usd: float | None = None, parent: "Task | str | None" = None,
               label: str = "") -> Task:
    ptask = get_task(parent) if isinstance(parent, str) else parent
    if budget_usd is None:
        budget = 0.0 if parent else (_num(_setting("MH_BUDGET_USD")) or 0.0)
    else:
        budget = _num(budget_usd) or 0.0
    home = _home()
    tid = _safe(f"{_safe(chat) or 'task'}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}", 120)
    t = Task(tid, chat or "", ptask.id if ptask else (parent if isinstance(parent, str) else None), budget, label,
             time.time(), home)
    t._parent_obj = ptask
    with _TASKS_LOCK:
        _TASKS[(str(home), tid)] = t
    try:
        meta = {"id": tid, "chat": t.chat, "parent": t.parent, "budget": budget, "label": label,
                "started": t.started}
        fsio.atomic_write_json(home / "usage" / "tasks" / f"{tid}.json", meta, fsync=False)
        if chat and not parent:
            fsio.atomic_write_json(home / "usage" / "current" / f"{_safe(chat, 120)}.json", {"task": tid},
                                   fsync=False)
        _prune(home)
    except Exception as e:
        _warn("start_task", e)
    return t


_PTR: dict[str, tuple] = {}


def _pointer_task(chat: str) -> Task | None:
    p = _home() / "usage" / "current" / f"{_safe(chat, 120)}.json"
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_mtime_ns, st.st_size, st.st_ino)
    hit = _PTR.get(str(p))
    if hit is None or hit[0] != key:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            tid = str(doc.get("task") or "") if isinstance(doc, dict) else ""
        except (OSError, ValueError):
            tid = ""
        _PTR[str(p)] = hit = (key, tid)
    return get_task(hit[1]) if hit[1] else None


def current() -> tuple[Task | None, str]:
    cur = _CUR.get()
    if cur and cur[0] is not None:
        return cur
    task, src = None, "background"
    tid = os.environ.get("MH_USAGE_TASK")
    if tid:
        task, src = get_task(tid), os.environ.get("MH_USAGE_SOURCE") or "chat"
    if task is None:
        chat = os.environ.get("MH_CHAT_ID")
        if chat:
            task = _pointer_task(chat)
            if task is not None:
                src = os.environ.get("MH_USAGE_SOURCE") or "chat"
    if task is None:
        src = "background"
    if cur:
        src = cur[1]
    return task, src


@contextlib.contextmanager
def bind(task: Task | None, source: str = "chat") -> Iterator[None]:
    tok = _CUR.set((task, source))
    try:
        yield
    finally:
        _CUR.reset(tok)


def over_budget(task) -> "BudgetExceeded | None":
    """The first task on the chain at or over its limit, as the BudgetExceeded to report; None when within it. For
    callers that cannot refuse a call up front (outside agents that make their own calls): check before a turn and
    stop the agent after the call that crosses."""
    if isinstance(task, str):
        task = get_task(task)
    for t in task.chain() if task is not None else []:
        if t.budget > 0:
            spent = t.spent()
            if spent >= t.budget:
                return BudgetExceeded(t.id, t.budget, spent)
    return None


def child_env(task: Task, source: str) -> dict:
    return {"MH_USAGE_TASK": task.id, "MH_USAGE_SOURCE": source, "MESHPOOL_HOME": str(C.home())}


# ---------------------------------------------------------------- budgets

_RES_LOCK = threading.Lock()
_RES: dict[str, dict[str, float]] = {}           # task id -> {reservation id: estimated budget cost}


class Reservation:
    def __init__(self, items: list[tuple[str, str]] | None = None):
        self._items = items or []
        self._closed = False

    def close(self) -> None:
        with _RES_LOCK:
            if self._closed:
                return
            self._closed = True
            for tid, rid in self._items:
                d = _RES.get(tid)
                if d is not None:
                    d.pop(rid, None)
                    if not d:
                        _RES.pop(tid, None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def reserve(model: str, *, request_chars: int = 0, max_tokens: int = 0, base: str = "", task=None) -> Reservation:
    """Refuse (BudgetExceeded) when the task or an ancestor is already at its limit counting calls in flight; else
    hold an estimate for this call. The call that crosses the limit is allowed."""
    if isinstance(task, str):
        task = get_task(task)
    if task is None:
        task = current()[0]
    if task is None:
        return Reservation()
    chain = [t for t in task.chain() if t.budget > 0]
    if not chain:
        return Reservation()
    est = budget_cost(model, estimate(request_chars, min(max_tokens or 1024, 4096) * 3), base)
    rid = uuid.uuid4().hex
    with _RES_LOCK:
        for t in chain:
            total = t.spent() + sum(_RES.get(t.id, {}).values())
            if total >= t.budget:
                raise BudgetExceeded(t.id, t.budget, total)
        for t in chain:
            _RES.setdefault(t.id, {})[rid] = est
    return Reservation([(t.id, rid) for t in chain])


# ---------------------------------------------------------------- writing

_Q_LOCK = threading.Lock()
_QUEUE: list[tuple[str, dict]] = []
_FLUSH_LOCK = threading.Lock()
_WAKE = threading.Event()
_WRITER: dict[str, Any] = {"thread": None}
_FLUSH_EVERY = 0.5


def _writer_loop() -> None:
    while True:
        _WAKE.wait(_FLUSH_EVERY)
        _WAKE.clear()
        flush()


def _ensure_writer() -> None:
    t = _WRITER["thread"]
    if t is not None and t.is_alive():
        return
    with _Q_LOCK:
        t = _WRITER["thread"]
        if t is None or not t.is_alive():
            t = threading.Thread(target=_writer_loop, name="usage-writer", daemon=True)
            _WRITER["thread"] = t
            t.start()


def flush() -> None:
    """Write everything queued so far (one append per file, under the file lock). Returns when it is on disk."""
    with _FLUSH_LOCK:
        with _Q_LOCK:
            items = _QUEUE[:]
            del _QUEUE[:]
        if not items:
            return
        by_path: dict[str, list[bytes]] = {}
        for path, rec in items:
            line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
            by_path.setdefault(path, []).append(line.encode("utf-8"))
        for path, lines in by_path.items():
            try:
                with fsio.file_lock(path):
                    with open(path, "ab") as f:
                        f.write(b"".join(lines))
                        f.flush()
            except Exception as e:
                _warn("write", e)


atexit.register(flush)


_MONTH_FILES: dict[tuple, str] = {}


def _month_file(home: Path, ts: float) -> str:
    month = time.strftime("%Y-%m", time.localtime(ts))
    k = (home, month)
    p = _MONTH_FILES.get(k)
    if p is None:
        p = _MONTH_FILES[k] = str(home / "usage" / f"calls-{month}.jsonl")
    return p


def record(model: str, raw_usage: dict | None, *, base: str = "", source: str | None = None, task=None,
           reservation: Reservation | None = None, request_chars: int = 0, reply_chars: int = 0,
           extra: dict | None = None) -> dict:
    """Account one model call. Never raises and never touches the disk or the network: the record is added to the
    in-process totals and queued for the writer thread."""
    rec: dict = {}
    try:
        if isinstance(task, str):
            task = get_task(task)
        cur_task, cur_src = current()
        if task is None:
            task = cur_task
        norm = normalize(raw_usage)
        est = False
        if not norm["reported"]:
            norm, est = estimate(request_chars, reply_chars), True
        cost, src = cost_of(model, norm, base)
        ts = time.time()
        host = ""
        if base:
            from urllib.parse import urlparse
            host = urlparse(base).netloc
        rec = {"v": 1, "id": uuid.uuid4().hex, "ts": ts,
               "chat": task.chat if task else os.environ.get("MH_CHAT_ID", ""),
               "task": task.id if task else "", "source": source or cur_src, "model": _bare(model), "host": host,
               "in": norm["in"], "cached": norm["cached"], "cache_write": norm["cache_write"], "out": norm["out"],
               "reasoning": norm["reasoning"], "reported": bool(norm["reported"]), "est": est, "cost": cost,
               "budget_cost": _budget_from(cost, norm), "price_src": src, "pid": os.getpid()}
        if extra:
            rec.update(extra)
        chain = task.chain() if task else []
        for t in chain:
            t._add_local(rec)
        if os.environ.get("MH_USAGE_LEDGER", "1").strip() != "0":
            with _Q_LOCK:
                _QUEUE.append((_month_file(_home(), ts), rec))
                for t in chain:
                    _QUEUE.append((t.spath, rec))
            _ensure_writer()
    except Exception as e:
        _warn("record", e)
    finally:
        if reservation is not None:
            with contextlib.suppress(Exception):
                reservation.close()
    return rec


# ---------------------------------------------------------------- reading


def read_records(since: float = 0, until: float | None = None) -> Iterator[dict]:
    """Records of the monthly ledger files covering [since, until), oldest first. Damaged lines are skipped."""
    d = _usage_dir()
    try:
        files = sorted(p for p in d.glob("calls-*.jsonl"))
    except OSError:
        return
    lo = time.strftime("%Y-%m", time.localtime(since)) if since else ""
    hi = time.strftime("%Y-%m", time.localtime(until)) if until else ""
    for p in files:
        month = p.stem[len("calls-"):]
        if (lo and month < lo) or (hi and month > hi):
            continue
        try:
            with open(p, "rb") as f:
                for line in f:
                    try:
                        r = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if not isinstance(r, dict):
                        continue
                    ts = r.get("ts")
                    if not isinstance(ts, (int, float)):
                        continue
                    if ts >= since and (until is None or ts < until):
                        yield r
        except OSError:
            continue
