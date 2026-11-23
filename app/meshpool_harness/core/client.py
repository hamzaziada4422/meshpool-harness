"""Model clients. LiveClient goes through eval/ledger.Ledger (budget guard + ledger); MockClient replays fixtures."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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
                                  r"input is too long|reduce the length|exceeds? the (?:model'?s? )?(?:context|maximum)|"
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
            r"|key_model_access_denied", m, re.I):
        return "model_missing"                      # an old chat on a model the endpoint no longer serves
    from meshpool_harness.core.context import is_context_error
    if is_context_error(m) and not re.search(r"HTTP 429|rate.?limit|per minute", m, re.I):
        return "context"                            # any status: llama.cpp and Ollama servers use 400, 500 or 200
    if "HTTP 413" in m or re.search(r"entity too large|payload too large|request too large", m, re.I):
        return "context"                            # the request is too big: shrink it like a context overflow
    if re.search(r"HTTP 402|budget (has been )?exceeded|budget_exceeded|exceeded your (current )?quota|"
                 r"insufficient[_ ](credits?|balance|funds|quota)|quota[_ ]exhausted|out of credits|"
                 r"credit balance is too low|max budget", m, re.I):
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


def rate_limit_wait(msg: str, n429: int, backoff: bool = True, cap: float | None = None) -> float:
    """Seconds to wait after the n-th 429 in a row: the reset time when the message gives one (up to cap), else
    3, 6, 12 ... 60 s (backoff; "please slow down" 429s clear in seconds) or 65 s (a token window). With no cap a
    retry-after is held to 300 s and a reset time to 120 s; the app agent passes what is left of its wait budget."""
    import calendar
    import re
    import time
    wait = min(60, 3 * 2 ** (max(1, n429) - 1)) if backoff else 65
    ra = re.search(r"\[retry-after: (\d+(?:\.\d+)?)\]", msg or "")   # the gateway said how long
    if ra:
        return max(1.0, min(300.0 if cap is None else max(1.0, cap), float(ra.group(1))))
    mt = re.search(r"resets at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", msg or "")
    if mt:
        reset = calendar.timegm(time.strptime(mt.group(1), "%Y-%m-%d %H:%M:%S"))
        wait = max(5, min(120.0 if cap is None else max(5.0, cap), reset - time.time() + 3))
    return wait


MAIN_PHASES = ("phase_2_build",)             # the user's own turn; every other phase is a side call
CONNECT_BUDGET_S = 120.0                     # a main turn gives up on a dead connection or 5xx after this long
SIDE_BUDGET_S = 15.0                         # compaction, classify, memory, summaries: two tries, then fail fast


def _tell_retry(text: str) -> None:
    import sys
    print(f"[Meshpool] {text}", file=sys.stderr, flush=True)


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

    def chat(self, *, model, messages, tools=None, task_id="", job_id="", role="worker", profile="deepseek",
             max_tokens=8192, temperature=None, extra=None):
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
        tighten, shrinks = 1.0, 0
        adapted: set = set()                     # refusals already answered by changing the request (once each)
        last = None
        side = (getattr(self, "phase", "phase_2_build") not in MAIN_PHASES) or role in ("router", "keepwarm")
        t_start, told = time.time(), False
        for attempt in range(3 if side else 25):  # sporadic 502s (backoff) and 429 token-window limits (wait for reset)
            try:
                send, sent_tools, out_tok = CTX.fit(messages, ex.get("tools"), win, max_tokens, tighten)
                ex2 = dict(ex, tools=sent_tools) if sent_tools is not None and "tools" in ex else ex
                r = self.ledger.chat(model=model, messages=send, profile=profile, task_id=task_id,
                                     role=role, job_id=job_id, max_tokens=out_tok, temperature=temperature,
                                     extra=ex2, retries=0)
                self._n429 = 0                                    # a success resets the 429 backoff
                self._check_silent_cut(model, send, sent_tools, r, win)
                _log_event(task_id, role, model, r)
                return r
            except RuntimeError as e:
                last = e
                msg = str(e)
                kind = classify_error(msg)
                if kind == "context":                              # too long: learn the real window, shrink, retry now
                    shrinks += 1
                    said = CTX.parse_limit(msg)
                    if said and said < win:
                        win = CTX.learn(model, said)
                        CTX.tell_once(model, win, self.notify or _tell_retry)
                    tighten *= 0.7
                    if shrinks > 4:
                        raise
                    continue
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
                                if f == "temperature":
                                    temperature = None
                                else:
                                    ex.pop(f, None)
                        continue
                if kind in ("bad_request", "vision", "gateway_reject") and attempt == 0:
                    from meshpool_harness.core.tools import clean      # invalid request: clean every message once
                    for m in messages:
                        if isinstance(m.get("content"), str):
                            m["content"] = clean(m["content"])
                    continue
                if kind == "gateway_reject" and attempt < 4:      # gateway rejects valid requests at random
                    time.sleep(2 * attempt)                        # (GATEWAY_ISSUES #10): retry the same request
                    continue
                if (kind == "gateway_reject" and os.environ.get("MH_400_WITHHOLD") == "1"
                        and _withhold_latest_output(messages)):
                    # The rejection is tied to content (e.g. hex or byte strings in a command's output; GATEWAY_ISSUES
                    # #12), so retrying the same request cannot pass. Withhold the newest tool output and tell the
                    # model, instead of crashing the whole run.
                    continue
                if kind == "rate_limit":                    # meshpool team token limit: wait for the window to reset
                    self._n429 = getattr(self, "_n429", 0) + 1
                    wait = rate_limit_wait(msg, self._n429, os.environ.get("MH_429_BACKOFF") == "1")
                    if side and (wait > SIDE_BUDGET_S or time.time() - t_start + wait > SIDE_BUDGET_S):
                        raise
                    time.sleep(wait)
                    continue
                if kind != "transient":
                    raise
                wait = min(30, 2 ** (attempt + 1))
                budget = SIDE_BUDGET_S if side else CONNECT_BUDGET_S
                if side and attempt >= 1 or time.time() - t_start + wait > budget:
                    raise                                       # out of budget: the user sees the real error now
                if not told and not side:
                    told = True
                    (self.notify or _tell_retry)(f"The model did not answer ({msg[:120]}). Retrying for up to "
                                                 f"{int(budget)} s.")
                time.sleep(min(wait, 2.0) if side else wait)
        raise last

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
        """Ollama and some local servers cut a too-long prompt without an error. The reply's prompt_tokens then sits far
        under what was sent: take that as the window, so the next request is trimmed to it."""
        try:
            from meshpool_harness.core import context as CTX
            used = int(((resp or {}).get("usage") or {}).get("prompt_tokens") or 0)
            sent_tok = CTX.estimate(sent) + CTX.estimate(tools or [])
            if used and sent_tok > 1500 and sent_tok > used * 1.6 and used >= 512:
                CTX.learn(model, int(used * 1.02) + CTX.max_output(win, None))
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

    def __init__(self, script: list[dict] | str):
        if isinstance(script, str):
            script = [json.loads(l) for l in Path(script).read_text().splitlines() if l.strip()]
        self.script = list(script)
        self.requests: list[dict] = []

    def chat(self, *, model, messages, tools=None, **kw):
        self.requests.append({"model": model, "messages": json.loads(json.dumps(messages)), "tools": tools, **kw})
        if not self.script:
            raise RuntimeError("MockClient script exhausted")
        msg = self.script.pop(0)
        return {"choices": [{"message": msg, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}

    def spent(self) -> float:
        return 0.0


def tool_call(name: str, args: dict, cid: str | None = None) -> dict:
    """Helper for writing mock scripts."""
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid or f"call_{name}_{abs(hash(json.dumps(args, sort_keys=True))) % 10**8}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}
