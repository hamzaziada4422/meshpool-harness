"""Model clients. LiveClient goes through eval/ledger.Ledger (budget guard + ledger); MockClient replays fixtures."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from meshpool_harness.core import usage

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WITHHELD = ("[output withheld: the API gateway rejected this command output, probably because of long hex, byte or "
            "key-like strings. Re-run the command so it prints decoded values or a short summary instead.]")


_HEXISH = None


def _hex_score(text: str) -> float:
    """Share of a text that looks like hex dumps, long hex runs or byte escapes (what the gateway's filter rejects)."""
    global _HEXISH
    if _HEXISH is None:
        import re
        _HEXISH = re.compile(r"(?:\b[0-9a-fA-F]{2}\b[ ]){6,}|[0-9a-fA-F]{16,}|(?:\\x[0-9a-fA-F]{2}){3,}")
    if not text:
        return 0.0
    return sum(len(m.group(0)) for m in _HEXISH.finditer(text)) / len(text)


def _withhold_latest_output(messages, limit=6) -> bool:
    """Withhold one tool output after a content-tied rejection: the most hex-like output anywhere in the history
    first (the trigger can be an old output; the filter is partly random), else the newest. At most `limit` per
    conversation, so a rejection caused by something else still ends the run instead of erasing the history."""
    if sum(1 for m in messages if m.get("content") == WITHHELD) >= limit:
        return False
    tools = [m for m in messages if m.get("role") == "tool" and m.get("content") != WITHHELD]
    if not tools:
        return False
    worst = max(tools, key=lambda m: _hex_score(str(m.get("content") or "")))
    target = worst if _hex_score(str(worst.get("content") or "")) > 0.05 else tools[-1]
    target["content"] = WITHHELD
    return True


_CONTEXT_RX = None


def classify_error(msg: str) -> str:
    """What a failed model call means, for the retry policy of both LiveClient.chat and the app agent (agent/core.py):
    'tls' (the endpoint's certificate is not trusted: retrying cannot help), 'auth' (key refused), 'model_missing' (the endpoint does not serve that model), 'quota' (a budget or quota is spent: never retried), 'rate_limit' (429), 'gateway_reject' (the gateway's 400 "rejected as invalid", which comes
    at random for valid requests: GATEWAY_ISSUES #10), 'context' (a 400 saying the request is longer than the model's
    context), 'vision' (a 4xx about images or content types), 'bad_request' (any other 4xx not named above: 400, 405, 409,
    415, 422 ...; the request itself is wrong, so it is not retried unchanged), 'transient' (5xx,
    connection lost, timeout) or 'fatal'."""
    global _CONTEXT_RX
    import re
    if _CONTEXT_RX is None:
        _CONTEXT_RX = (re.compile(r"context[ _-]?(?:length|window)|maximum context|too many tokens|prompt is too long|"
                                  r"input is too long|reduce the length|exceeds? the (?:model'?s? )?(?:context|maximum (?:context|input|prompt|number of (?:input )?tokens|length))|"
                                  r"token limit", re.I),
                       re.compile(r"image|vision|multi-?modal|content[ _-]?type|content part", re.I))
    m = msg or ""
    from meshpool_harness.core import refusals as RF
    if RF.is_tls_error(m):
        return "tls"
    if "HTTP 401" in m or "HTTP 403" in m:
        return "auth"
    if ("HTTP 400" in m or "HTTP 404" in m) and re.search(
            r"(?:invalid|unknown|unsupported|no such|not a valid) model|model[ _-]?not[ _-]?found|model\b[^.\n]{0,80}?"
            r"(?:not found|does not exist|doesn't exist|is not available|not supported|no longer|unavailable)"
            r"|key_model_access_denied|deployment ?not ?found|deployment[^.\n]{0,60}(?:does not exist|not found)", m, re.I):
        return "model_missing"                      # an old chat on a model the endpoint no longer serves
    from meshpool_harness.core.context import is_context_error
    if is_context_error(m) and not re.search(r"HTTP 429|rate.?limit|per minute", m, re.I):
        return "context"                            # any status: llama.cpp and Ollama servers use 400, 500 or 200
    if "HTTP 413" in m or re.search(r"entity too large|payload too large|request too large", m, re.I):
        return "context"                            # the request is too big: shrink it like a context overflow
    # A thinking/reasoning/token "budget" is a request setting, not money: a 400 about it is a plain bad request.
    money = re.sub(r"(?:thinking|reasoning|token|output)[ _.-]*budget\w*|budget[_ ]tokens", " ", m, flags=re.I)
    if re.search(r"HTTP 402|budget (has been )?exceeded|budget_exceeded|exceededbudget|exceeded your (current )?quota|"
                 r"insufficient[_ ](credits?|balance|funds|quota)|quota[_ ]exhausted|out of credits|"
                 r"credit balance is too low|max budget (?:for|of|has|reached)", money, re.I):
        return "quota"                              # spent: waiting or retrying cannot help (UHP: quota_exhausted)
    if "HTTP 429" in m:
        return "rate_limit"
    if "rejected as invalid" in m:
        return "gateway_reject"
    if re.search(r"HTTP 4\d\d", m):
        if _CONTEXT_RX[0].search(m):
            return "context"
        if _CONTEXT_RX[1].search(m):
            return "vision"
        return "bad_request"
    if any(code in m for code in ("HTTP 5", "HTTP 0", "upstream", "timed out")):
        return "transient"
    return "fatal"


def stated_wait(msg: str) -> float | None:
    """The wait (seconds, uncapped, >= 0) the server stated in an error text, or None when it stated nothing:
    `[retry-after-ms: N]`, `[retry-after: N]` (delta seconds), `[retry-after: <HTTP-date>]`, or the gateway's
    "resets at: YYYY-MM-DD HH:MM:SS" (UTC, converted to seconds from now, plus 3 s of slack)."""
    import calendar
    import re
    import time
    text = msg or ""
    try:
        ms = re.search(r"\[retry-after-ms:\s*(\d+(?:\.\d+)?)\s*\]", text, re.I)
        if ms:
            return float(ms.group(1)) / 1000.0
        ra = re.search(r"\[retry-after:\s*([^\]]+?)\s*\]", text, re.I)
        if ra:
            val = ra.group(1)
            if re.fullmatch(r"\d+(?:\.\d+)?", val):
                return float(val)
            from email.utils import parsedate_to_datetime
            from datetime import timezone
            dt = parsedate_to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, dt.timestamp() - time.time())
        mt = re.search(r"resets at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", text)
        if mt:
            reset = calendar.timegm(time.strptime(mt.group(1), "%Y-%m-%d %H:%M:%S"))
            return max(0.0, reset - time.time() + 3)
    except (ValueError, TypeError, OverflowError, IndexError):
        return None
    return None


def rate_limit_wait(msg: str, n429: int, backoff: bool = True, cap: float | None = None) -> float:
    """Seconds to wait after the n-th 429 in a row: the stated wait when the message gives one (see stated_wait; up to
    cap), else 3, 6, 12 ... 60 s (backoff; "please slow down" 429s clear in seconds) or 65 s (a token window). With
    no cap a retry-after is held to 300 s and a reset time to 120 s; the app agent passes what is left of its wait
    budget."""
    import re
    wait = min(60, 3 * 2 ** (max(1, n429) - 1)) if backoff else 65
    stated = stated_wait(msg)
    if stated is None:
        return wait
    if re.search(r"\[retry-after(?:-ms)?:", msg or "", re.I):   # the gateway said how long
        return max(1.0, min(300.0 if cap is None else max(1.0, cap), stated))
    return max(5.0, min(120.0 if cap is None else max(5.0, cap), stated))


MAIN_PHASES = ("phase_2_build",)             # the user's own turn; every other phase is a side call
CONNECT_BUDGET_S = 120.0                     # a main turn gives up on a dead connection or 5xx after this long
SIDE_BUDGET_S = 15.0                         # compaction, classify, memory, summaries: two tries, then fail fast


def _reply_chars(r) -> int:
    """Characters in a reply's text, reasoning and tool-call arguments (the estimate when no usage came back)."""
    try:
        m = r["choices"][0]["message"]
        n = len(m.get("content") or "") + len(m.get("reasoning_content") or "")
        return n + sum(len((c.get("function") or {}).get("arguments") or "") for c in m.get("tool_calls") or [])
    except (KeyError, IndexError, TypeError, AttributeError):
        return 0


def _request_chars(messages) -> int:
    try:
        return len(json.dumps(messages))
    except (TypeError, ValueError):
        return 0


def _tell_retry(text: str) -> None:
    import sys
    print(f"[Meshpool] {text}", file=sys.stderr, flush=True)


_PROBE_GUARD = threading.Lock()
_PROBE_LOCKS: dict = {}          # (endpoint, model) -> lock held by the first, probing call
_PROVEN: set = set()             # keys that have had one successful reply: no lock needed any more


def _reset_probe() -> None:
    with _PROBE_GUARD:
        _PROBE_LOCKS.clear()
        _PROVEN.clear()


class LiveClient:
    notify = None                            # optional callable(text): told once after the first failure that a retry follows

    def __init__(self, phase: str = "phase_2_build"):
        self.phase = phase
        try:
            from eval.ledger import Ledger          # benchmark runs: budget ledger + accounting
            self.ledger = Ledger(phase)
        except ImportError:                         # installed on its own: plain transport
            from meshpool_harness.core.wire import SimpleLedger
            self.ledger = SimpleLedger(phase)

    def chat(self, *, model, messages, **kw):
        """S-03: until an (endpoint, model) has had one successful reply, only one call at a time runs it, so a refused
        parameter is learned by that one probe (then dropped by every waiter) instead of 400-ing once per parallel thread."""
        from meshpool_harness.core import modelcaps as MC
        key = MC.endpoint_key(getattr(self.ledger, "base_url", None), model)
        if key in _PROVEN:
            return self._chat_send(model=model, messages=messages, **kw)
        with _PROBE_GUARD:
            lock = _PROBE_LOCKS.setdefault(key, threading.RLock())
        with lock:                                        # held for the whole send/learn/resend loop; freed on any exit
            r = self._chat_send(model=model, messages=messages, **kw)   # re-reads MC.refused() itself, after the lock
            _PROVEN.add(key)
            return r

    def _chat_send(self, *, model, messages, tools=None, task_id="", job_id="", role="worker", profile="deepseek",
              max_tokens=8192, temperature=None, extra=None, attempts: int | None = None):
        allow = [m.strip() for m in os.environ.get("MH_MODEL_ALLOW", "").split(",") if m.strip()]
        if allow and model not in allow:                 # a cost cap: only these models may be called; a fallback
            raise RuntimeError(f"model {model} not allowed (MH_MODEL_ALLOW={','.join(allow)})")   # chain moves on
        from meshpool_harness.profiles import apply, for_model
        prof = for_model(model, os.environ.get("MH_PROFILE"))
        if prof["family"] != "deepseek":
            _b = {"max_tokens": max_tokens} if max_tokens else {}
            if temperature is not None:
                _b["temperature"] = temperature
            messages, b = apply(prof, messages, _b, role if role in ("planner", "verifier") else "worker")
            temperature = b.get("temperature")
            profile = prof["family"]
            _mct = b.get("max_completion_tokens")             # profile limits.param: models that reject max_tokens
        else:
            _mct = None
        ex = dict(extra or {})
        if _mct:
            ex["max_completion_tokens"] = _mct
        if tools:
            ex["tools"] = tools
            ex.setdefault("tool_choice", "auto")
        import time
        from meshpool_harness.core import context as CTX
        base_url = getattr(self.ledger, "base_url", None)
        win = CTX.window(model, base_url, getattr(self.ledger, "api_key", "") or "")
        CTX.tell_once(model, win, self.notify or _tell_retry)
        from meshpool_harness.core import modelcaps as MC, models as MD
        if MC.is_missing(base_url, model):               # learned from an earlier 404: do not call (nothing is billed)
            raise RuntimeError(f"meshpool HTTP 404: model {model} is not deployed on this endpoint (remembered; not called)")
        gone, mct = MC.refused(base_url, model), False   # fields this endpoint refused for this model before
        if "temperature" in gone:
            temperature = None
        mct = "max_tokens" in gone
        for k in gone:
            ex.pop(k, None)
        tried: set = set()
        tighten, shrinks = 1.0, 0
        adapted: set = set()                     # refusals already answered by changing the request (once each)
        max_tokens, temperature = self._apply_learned(model, ex, max_tokens, temperature)   # right the first time
        last = None
        side = (getattr(self, "phase", "phase_2_build") not in MAIN_PHASES) or role in ("router", "keepwarm")
        t_start, told = time.time(), False
        # The attempts (3 for a side call, 25 otherwise) are for failures that are not rate limits (sporadic 502s,
        # rejected requests). A 429 never uses one up: it is bounded by the per-call wait budget, so a long but
        # in-budget wait cannot end the loop early, and an endless 429 stream ends when the budget is spent.
        try:
            rl_budget = max(0.0, float(os.environ.get("MH_RATE_LIMIT_WAIT_S", "1800")))
        except ValueError:
            rl_budget = 1800.0
        waited, attempt, stripped_reasoning = 0.0, 0, False
        while attempt < (attempts or (3 if side else 25)):
            try:
                send, sent_tools, out_tok = CTX.fit(messages, ex.get("tools"), win, max_tokens, tighten)
                ex2 = dict(ex, tools=sent_tools) if sent_tools is not None and "tools" in ex else ex
                if mct:                                       # a model that refuses max_tokens takes the newer name
                    ex2 = dict(ex2, max_completion_tokens=out_tok)
                req_chars = _request_chars(send)
                ubase = getattr(self.ledger, "base_url", "") or ""
                res = usage.reserve(model, request_chars=req_chars, max_tokens=out_tok, base=ubase)   # may raise
                from meshpool_harness.core.wire import SimpleLedger
                sk = {"side": side} if isinstance(self.ledger, SimpleLedger) else {}   # side: no waiting out an outage
                try:
                    r = self.ledger.chat(model=model, messages=send, profile=profile, task_id=task_id,
                                         role=role, job_id=job_id, max_tokens=out_tok, temperature=temperature,
                                         extra=ex2, retries=0, **sk)
                except BaseException:
                    res.close()
                    raise
                usage.record(model, r.get("usage"), base=ubase, reservation=res, request_chars=req_chars,
                             reply_chars=_reply_chars(r),
                             source=os.environ.get("MH_USAGE_SOURCE")
                             or ("engine" if self.phase in MAIN_PHASES else "helper:" + self.phase))
                self._n429 = 0                                    # a success resets the 429 backoff
                self._check_silent_cut(model, send, sent_tools, r, win)
                _log_event(task_id, role, model, r)
                return r
            except RuntimeError as e:
                last = e
                msg = str(e)
                kind = classify_error(msg)
                cur = attempt
                if kind != "rate_limit":
                    attempt += 1
                if kind == "bad_request" and "reasoning_content" in msg and not stripped_reasoning:
                    stripped_reasoning = True                  # the server refuses the field: drop it, retry once
                    for m in messages:
                        if isinstance(m, dict):
                            m.pop("reasoning_content", None)
                    continue
                if kind == "tls":                                  # an untrusted certificate: say how to trust it
                    from meshpool_harness.core import refusals as RF
                    raise RuntimeError("The endpoint's TLS certificate is not trusted. "
                                       + (RF.diagnose(msg) or {}).get("note", "") + " (" + msg[:160] + ")")
                if kind == "context":                              # an output-size error is not a long conversation
                    cfix = self._adapt_refusal(msg, dict(ex, model=model, messages=messages, max_tokens=max_tokens),
                                               model, adapted)
                    if cfix and cfix.get("action") == "max_out":
                        max_tokens = min(max_tokens, int(cfix["max_output"]))
                        if "max_completion_tokens" in ex:
                            ex["max_completion_tokens"] = max_tokens
                        if cfix.get("context_window"):
                            win = CTX.learn(model, int(cfix["context_window"])) if int(cfix["context_window"]) < win else win
                        continue
                if kind == "context":                              # too long: learn the real window, shrink, retry now
                    shrinks += 1
                    said = CTX.parse_limit(msg)
                    if said and said < win:
                        win = CTX.learn(model, said, explicit=True)
                        CTX.tell_once(model, win, self.notify or _tell_retry)
                    tighten *= 0.7
                    if shrinks > 4:
                        raise
                    continue
                if kind == "model_missing":                        # not deployed here: remember it, never retry
                    MC.learn_missing(base_url, model)
                    raise
                if kind == "vision" and any(isinstance(m.get("content"), list) and any(
                        isinstance(p, dict) and p.get("type") in ("image_url", "input_image", "image") for p in m["content"])
                        for m in messages):
                    MC.learn_no_vision(base_url, model)            # text-only here: image reviews skip it from now on
                    raise
                if kind == "bad_request":                          # the endpoint named what it cannot take: fix, not resend
                    body = dict(ex, model=model, messages=messages, max_tokens=max_tokens)
                    if temperature is not None:
                        body["temperature"] = temperature
                    fix = self._adapt_refusal(msg, body, model, adapted)
                    if fix:
                        if fix.get("action") == "max_out":
                            max_tokens = min(max_tokens, int(fix["max_output"]))
                            if "max_completion_tokens" in ex:
                                ex["max_completion_tokens"] = max_tokens
                        elif fix.get("action") == "max_tokens_param":
                            ex["max_completion_tokens"] = max_tokens
                        elif fix.get("action") == "no_tools":
                            ex.pop("tools", None)
                            ex.pop("tool_choice", None)
                        elif fix.get("action") == "drop_field":
                            for f in fix["fields"]:
                                MC.learn_refused(base_url, model, f)   # persisted per endpoint: the next client never sends it
                                if f == "temperature":
                                    temperature = None
                                else:
                                    ex.pop(f, None)
                        continue
                if "HTTP 400" in msg:                              # a refused field: drop it and resend at once
                    sent = set(ex) | {"max_tokens"} | ({"temperature"} if temperature is not None else set()) | (
                        {"prompt_cache_key", "user"} if os.environ.get("MH_CACHE_KEY") == "1" and task_id else set())
                    f = MD.unsupported_param(msg, sent)
                    if f and f not in tried:
                        tried.add(f)
                        MC.learn_refused(base_url, model, f)
                        ex.pop(f, None)
                        if f == "temperature":
                            temperature = None
                        elif f == "max_tokens":
                            mct = True
                        continue
                if kind in ("bad_request", "vision", "gateway_reject") and cur == 0:
                    from meshpool_harness.core.tools import clean      # invalid request: clean every message once
                    for m in messages:
                        if isinstance(m.get("content"), str):
                            m["content"] = clean(m["content"])
                    continue
                if kind == "gateway_reject" and cur < 4:      # gateway rejects valid requests at random
                    time.sleep(2 * cur)                        # (GATEWAY_ISSUES #10): retry the same request
                    continue
                if (kind == "gateway_reject" and os.environ.get("MH_400_WITHHOLD") == "1"
                        and _withhold_latest_output(messages)):
                    # The rejection is tied to content (e.g. hex or byte strings in a command's output; GATEWAY_ISSUES
                    # #12), so retrying the same request cannot pass. Withhold the newest tool output and tell the
                    # model, instead of crashing the whole run.
                    continue
                if kind == "rate_limit":                    # meshpool team token limit: wait for the window to reset
                    self._n429 = getattr(self, "_n429", 0) + 1
                    left = rl_budget - waited
                    stated = stated_wait(msg)
                    if stated is not None:                      # the server said when the window resets: wait that
                        wait = max(1.0, stated)
                        if wait > left:
                            raise RuntimeError(f"{msg} [rate limit: it resets in {wait:.0f}s, after the {rl_budget:.0f}s "
                                               f"wait budget (MH_RATE_LIMIT_WAIT_S); {left:.0f}s left]") from e
                    else:
                        if left <= 0:
                            raise RuntimeError(f"{msg} [rate limit: the {rl_budget:.0f}s wait budget "
                                               f"(MH_RATE_LIMIT_WAIT_S) is spent]") from e
                        wait = min(left, rate_limit_wait(msg, self._n429, os.environ.get("MH_429_BACKOFF") == "1"))
                    if side and (wait > SIDE_BUDGET_S or time.time() - t_start + wait > SIDE_BUDGET_S):
                        raise
                    waited += wait
                    time.sleep(wait)
                    continue
                if kind != "transient":
                    raise
                wait = min(30, 2 ** (cur + 1))
                budget = SIDE_BUDGET_S if side else CONNECT_BUDGET_S
                if side and cur >= 1 or time.time() - t_start + wait > budget:
                    raise                                       # out of budget: the user sees the real error now
                if not told and not side:
                    told = True
                    (self.notify or _tell_retry)(f"The model did not answer ({msg[:120]}). Retrying for up to "
                                                 f"{int(budget)} s.")
                time.sleep(min(wait, 2.0) if side else wait)
        raise last

    @staticmethod
    def _apply_learned(model, ex: dict, max_tokens, temperature):
        """What this endpoint+model already refused (modelcaps, persisted): applied before the first request."""
        try:
            from meshpool_harness import providers as PV
            from meshpool_harness.core import modelcaps as MC
            c = MC.resolve(PV.key_base(), model)
            if c.source.get("max_output") == "learned" and max_tokens:
                max_tokens = min(int(max_tokens), int(c.max_output))
            for f in c.refused:
                if f == "temperature":
                    temperature = None
                elif f not in ("model", "messages", "max_tokens"):
                    ex.pop(f, None)
            if c.max_tokens_param == "max_completion_tokens" or "max_tokens" in c.refused:
                ex["max_completion_tokens"] = max_tokens
            elif "max_completion_tokens" in ex and max_tokens:
                ex["max_completion_tokens"] = min(int(ex["max_completion_tokens"]), int(max_tokens))
            if c.no_tools:
                ex.pop("tools", None)
                ex.pop("tool_choice", None)
        except Exception:  # noqa: BLE001 - what was learned is an optimisation
            pass
        return max_tokens, temperature

    @staticmethod
    def _adapt_refusal(msg: str, body: dict, model: str, adapted: set) -> dict | None:
        """refusals.diagnose for the request-fixable actions, once per field/action in one call; what it shows is
        remembered per endpoint+model (modelcaps.learn). None: nothing to change, fall through to the old handling."""
        from meshpool_harness.core import modelcaps as MC
        from meshpool_harness.core import refusals as RF
        d = RF.diagnose(msg, body)
        if not d or d["action"] not in ("drop_field", "max_tokens_param", "max_out", "no_tools"):
            return None
        if d["action"] == "drop_field":
            d["fields"] = [f for f in d["fields"] if ("drop", f) not in adapted]
            if not d["fields"]:
                return None
            adapted.update(("drop", f) for f in d["fields"])
            d["learn"] = {"refused": list(d["fields"])}
        else:
            if (d["action"],) in adapted:
                return None
            adapted.add((d["action"],))
        try:
            from meshpool_harness import providers as PV
            MC.learn(PV.key_base(), model, **d["learn"])
        except Exception:  # noqa: BLE001
            pass
        return d

    def _check_silent_cut(self, model, sent, tools, resp, win) -> None:
        """Ollama and some local servers cut a too-long prompt without an error (core/context.observe_silent_cut judges
        it: local endpoints only, total prompt tokens, two matching replies, a sane floor)."""
        try:
            from meshpool_harness.core import context as CTX
            CTX.observe_silent_cut(model, CTX.estimate(sent) + CTX.estimate(tools or []), (resp or {}).get("usage") or {},
                                   win, getattr(self.ledger, "base_url", None))
        except Exception:  # noqa: BLE001
            pass

    def spent(self) -> float | None:
        st = getattr(self.ledger, "state", None)          # SimpleLedger (stand-alone install) has no spend state
        return getattr(st, "since_start", None) if st is not None else None


def _log_event(task_id: str, role: str, model: str, resp: dict) -> None:
    """One JSON line per model call to MH_EVENT_LOG (the web app tails it for live progress)."""
    path = os.environ.get("MH_EVENT_LOG")
    if not path:
        return
    try:
        import time
        msg = ((resp.get("choices") or [{}])[0].get("message") or {})
        calls = []
        for c in msg.get("tool_calls") or []:
            f = c.get("function") or {}
            args = f.get("arguments") or ""
            try:
                a = json.loads(args)
                short = a.get("command") or a.get("path") or a.get("pattern") or a.get("answer") or a.get("name") \
                    or a.get("url") or a.get("action") or args
            except (ValueError, AttributeError):
                short = args
            calls.append({"name": f.get("name", ""), "args": str(short)[:400]})
        u = resp.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
        ev = {"t": time.time(), "task": task_id, "role": role, "model": model,
              "text": (msg.get("content") or "")[:1200], "tools": calls,
              "in": u.get("prompt_tokens", 0), "cached": cached, "out": u.get("completion_tokens", 0)}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
    except Exception:
        pass


class MockClient:
    """Replays a list of assistant messages (OpenAI format). Records every request for assertions."""

    def __init__(self, script: list[dict] | str, usage: dict | list | None = None):
        """usage: the usage dict of every reply, or a list with one per reply (the last one repeats)."""
        if isinstance(script, str):
            script = [json.loads(l) for l in Path(script).read_text().splitlines() if l.strip()]
        self.script = list(script)
        self.requests: list[dict] = []
        self._usage = usage if usage is not None else {"prompt_tokens": 100, "completion_tokens": 20}
        self._n = 0

    def chat(self, *, model, messages, tools=None, **kw):
        req_chars = _request_chars(messages)
        res = usage.reserve(model, request_chars=req_chars, max_tokens=kw.get("max_tokens") or 0)   # may raise
        self.requests.append({"model": model, "messages": json.loads(json.dumps(messages)), "tools": tools, **kw})
        if not self.script:
            res.close()
            raise RuntimeError("MockClient script exhausted")
        msg = self.script.pop(0)
        u = self._usage[min(self._n, len(self._usage) - 1)] if isinstance(self._usage, list) else self._usage
        self._n += 1
        out = {"choices": [{"message": msg, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
               "usage": u}
        usage.record(model, u, reservation=res, request_chars=req_chars, reply_chars=_reply_chars(out),
                     source=os.environ.get("MH_USAGE_SOURCE") or "engine")
        return out

    def spent(self) -> float:
        return 0.0


def tool_call(name: str, args: dict, cid: str | None = None) -> dict:
    """Helper for writing mock scripts."""
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid or f"call_{name}_{abs(hash(json.dumps(args, sort_keys=True))) % 10**8}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}
