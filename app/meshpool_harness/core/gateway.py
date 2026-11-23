"""Client-side handling of a gateway that stalls (Harness v4, gateway health).

Shared by every chat and sub-agent in the process:

- A Gate per endpoint and key: how many model calls may be open at once. A gateway that leaks a slot for every
  abandoned request locks the key once its slots run out, so the number of open calls is capped. MH_MAX_INFLIGHT
  sets a fixed cap; without it the cap follows the provider (see provider_cap). A 429 halves the live limit and
  every 10 good calls give one back. While a model on the key is recovering from an outage, calls go one at a time;
  the limit opens again after three calls go through.
- A marker file per endpoint, key and model that is down, so other processes (engines, helpers) see it too.
- A Health per endpoint, key and model: is it serving? One tiny probe after a failure tells a stuck endpoint (the
  probe fails too) from a request that is only slow (the probe answers). A gateway can fail one model and serve the
  rest, so each model has its own: one model's outage never holds back a chat on another model. While it is down,
  one caller probes at a time on a backoff shared by every caller, and the others wait for its answer instead of
  sending requests that would only pile up behind it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from urllib.parse import urlparse

PROBE_REUSE_S = 15
BACKOFF_S = 10                                      # gaps between probes while down: 10, 20 ... 60 s


_CFG = {"t": 0.0, "v": None}


def _fixed_inflight() -> int | None:
    """MH_MAX_INFLIGHT as a fixed cap, or None when it is not set (the cap then follows the provider). Read from the
    config file at most every 2 s, so a change in Settings reaches this process and the assistant servers without a
    restart; else the environment. A saved but empty field means "not set": the file decides, not a stale
    environment."""
    now = time.time()
    if now - _CFG["t"] > 2:
        _CFG["t"] = now
        try:
            from meshpool_harness import config as C
            vals = C.read()
            _CFG["v"] = (vals.get("MH_MAX_INFLIGHT") or "") if "MH_MAX_INFLIGHT" in vals else None
        except Exception:  # noqa: BLE001
            _CFG["v"] = None
    raw = _CFG["v"] if _CFG["v"] is not None else os.environ.get("MH_MAX_INFLIGHT")
    try:
        return max(1, int(raw)) if raw else None
    except ValueError:
        return None


def _max_inflight() -> int:
    """The fixed cap, or 4 when none is set."""
    f = _fixed_inflight()
    return 4 if f is None else f


_CLOUD_HOSTS = {"api.openai.com", "api.anthropic.com", "api.deepseek.com", "openrouter.ai",
                "generativelanguage.googleapis.com", "api.x.ai", "api.moonshot.ai", "open.bigmodel.cn"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _host(base: str) -> str:
    try:
        return (urlparse(base or "").hostname or "").lower()
    except ValueError:
        return ""


def provider_cap(base: str) -> int:
    """How many model calls an endpoint takes at once when MH_MAX_INFLIGHT is not set: the Meshpool gateway 16, the
    known cloud APIs 32, a local server 2, anything else 8."""
    host = _host(base)
    if host == "meshpool.ai" or host.endswith(".meshpool.ai"):
        return 16
    if host in _CLOUD_HOSTS:
        return 32
    if host in _LOCAL_HOSTS:
        return 2
    return 8


def default_slots_dir() -> str:
    """The folder every Meshpool process shares for its slot lock files unless MH_SLOTS_DIR names another."""
    import tempfile
    return os.path.join(tempfile.gettempdir(), "meshpool-gateway-slots")


class _Slots:
    """MH_MAX_INFLIGHT across processes: the app and every assistant server (where sub-agents run) take one of N
    lock files per endpoint and key. The operating system drops a lock when its process ends, so a crash never
    leaks a slot. MH_SLOTS_DIR moves the folder (tests point it at their own tmp_path)."""
    def __init__(self, name: str):
        self.dir = os.environ.get("MH_SLOTS_DIR") or default_slots_dir()
        self.name = name

    def _try(self, i: int):
        path = os.path.join(self.dir, f"{self.name}-{i}.lock")
        f = open(path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            f.close()
            return None

    def take(self, n, check_stop, waiting):
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError:
            return None                              # no shared folder: the in-process gate still holds
        told = False
        while True:
            for i in range(n() if callable(n) else n):         # the setting as it is now, on every pass
                try:
                    f = self._try(i)
                except OSError:
                    return None
                if f:
                    return f
            if not told:
                told = True
                waiting()
            time.sleep(0.25)
            check_stop()

    @staticmethod
    def give(f):
        if f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            f.close()                                # closing drops a POSIX flock


class Gate:
    def __init__(self, name: str = "", base: str = ""):
        self.name = name
        self.slots = _Slots(name) if name and os.environ.get("MH_SHARED_SLOTS", "1") == "1" else None
        self.cond = threading.Condition()
        self.inflight = 0
        self.waiting = 0                             # calls waiting for a slot right now
        self.degraded = False
        self.ok_streak = 0
        self.base_cap = provider_cap(base)
        p = urlparse(base or "") if base else None
        try:
            self.host = (p.hostname or "") + (f":{p.port}" if p and p.port else "") if p else ""
        except ValueError:
            self.host = ""
        self._live: int | None = None                # lowered after a 429; None: at the cap
        self._ups = 0                                # good calls in a row since it was lowered

    @property
    def cap(self) -> int:
        """The most this key takes: MH_MAX_INFLIGHT as it is now, else what the provider takes."""
        fixed = _fixed_inflight()
        return self.base_cap if fixed is None else fixed

    @property
    def limit(self) -> int:
        if self.degraded:
            return 1
        cap = self.cap
        return cap if self._live is None else max(1, min(cap, self._live))

    def degrade(self):
        with self.cond:
            self.degraded, self.ok_streak = True, 0  # while something recovers: one call at a time

    def throttled(self):
        """The endpoint said 429 or "too many concurrent requests": halve the live limit."""
        with self.cond:
            cur = self.cap if self._live is None else min(self.cap, self._live)
            self._live, self._ups = max(1, cur // 2), 0

    def acquire(self, check_stop, emit=None):
        t0 = time.time()
        st = {"told": False, "waited": False, "warned": False}

        def announce():
            st["waited"] = True
            if emit and not st["told"]:
                st["told"] = True
                emit({"type": "gate", "state": "waiting", "inflight": self.inflight, "limit": self.limit})

        def tick():
            check_stop()
            if not emit and not st["warned"] and time.time() - t0 > 2:
                st["warned"] = True
                print(f"[Meshpool] waiting for a free model-call slot ({self.inflight} running, limit "
                      f"{self.limit})", file=sys.stderr, flush=True)

        with self.cond:
            counted = False
            try:
                while self.inflight >= self.limit:
                    if not counted:
                        counted = True
                        self.waiting += 1
                        if emit:
                            emit({"type": "status", "text": "Waiting for another chat's model call to finish (the "
                                                            "endpoint is recovering, so calls go one at a time)."
                                                            if self.degraded else
                                                            f"Waiting for a free slot: {self.inflight} model calls "
                                                            "are already running on this key (MH_MAX_INFLIGHT)."})
                        announce()
                    self.cond.wait(0.25)
                    self.cond.release()
                    try:
                        tick()
                    finally:
                        self.cond.acquire()
            finally:
                if counted:
                    self.waiting -= 1
            self.inflight += 1
        slot = None
        if self.slots is not None:
            shared = [False]

            def wait_shared():
                shared[0] = True
                with self.cond:
                    self.waiting += 1
                if emit:
                    emit({"type": "status", "text": "Waiting for a free slot: sub-agents or another Meshpool window "
                                                    "are using this key's model calls (MH_MAX_INFLIGHT)."})
                announce()
            try:
                slot = self.slots.take(lambda: self.limit, tick, wait_shared)
            except BaseException:
                with self.cond:                      # Stop while waiting for a shared slot: give the local one back
                    self.inflight = max(0, self.inflight - 1)
                    self.cond.notify_all()
                raise
            finally:
                if shared[0]:
                    with self.cond:
                        self.waiting -= 1
        if emit and st["waited"]:
            emit({"type": "gate", "state": "acquired", "waited_s": round(time.time() - t0, 1)})
        return slot

    def release(self, ok: bool, slot=None):
        _Slots.give(slot)
        with self.cond:
            self.inflight = max(0, self.inflight - 1)
            if ok:
                self.ok_streak += 1
                if self.ok_streak >= 3:
                    self.degraded = False
                if self._live is not None:
                    self._ups += 1
                    if self._ups >= 10:              # ten good calls in a row: give one slot back
                        self._ups = 0
                        self._live += 1
                        if self._live >= self.cap:
                            self._live = None
            else:
                self._ups = 0
            self.cond.notify_all()


class Health:
    def __init__(self, gate: Gate | None = None, model: str = ""):
        self.gate = gate or Gate()
        self.model = model
        self.cond = threading.Condition()
        self.down_since = 0.0
        self.prober = False
        self.last_probe = (0.0, "")
        self.next_probe = 0.0                       # while down: no one probes before this (shared backoff)
        self.fails = 0
        self.up_at = 0.0
        self.waiters = 0                            # callers inside wait_until_up

    # the gate is shared by every model on the key
    @property
    def inflight(self) -> int:
        return self.gate.inflight

    @property
    def limit(self) -> int:
        return self.gate.limit

    def acquire(self, check_stop, emit=None):
        return self.gate.acquire(check_stop, emit)

    def release(self, ok: bool, slot=None):
        if ok:
            with self.cond:
                if self._is_down_locked():
                    self._mark_up()
        self.gate.release(ok, slot)

    # ---------- is it up -----------------------------------------------------------------------------------------
    def is_down(self) -> bool:
        """Down in this process, or marked down by another process (a fresh marker file) in the last 30 minutes."""
        with self.cond:
            return self._is_down_locked()

    def _is_down_locked(self) -> bool:
        return self.down_since > 0 or self._marker_fresh()

    # The marker: one small file per gate and model in the slots folder, so an engine or helper process sees an
    # endpoint the app found down. Reading or writing it never raises.
    def _marker_path(self) -> str | None:
        if not self.gate.name:
            return None
        d = os.environ.get("MH_SLOTS_DIR") or default_slots_dir()
        return os.path.join(d, f"{self.gate.name}-{hashlib.sha256(self.model.encode('utf-8')).hexdigest()[:8]}.down")

    def _write_marker(self):
        path = self._marker_path()
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"since": self.down_since or time.time(), "next_probe": self.next_probe,
                           "pid": os.getpid()}, f)
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001
            pass

    def _clear_marker(self):
        path = self._marker_path()
        if path:
            try:
                os.remove(path)
            except Exception:  # noqa: BLE001
                pass

    def _marker_fresh(self) -> bool:
        path = self._marker_path()
        if not path:
            return False
        try:
            window = float(os.environ.get("MH_DOWN_MARKER_S") or 1800)
        except ValueError:
            window = 1800.0
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("pid") != os.getpid() and time.time() - float(d.get("since") or 0) < window
        except Exception:  # noqa: BLE001
            return False

    def check(self, probe, wait_s: float = 60, check_stop=None) -> str:
        """"up", "down" or "auth" after a call failed: a probe taken in the last few seconds, another caller's probe
        that is running now, or a fresh one. While the endpoint is already marked down this does not probe: the
        caller goes on to wait_until_up."""
        end = time.time() + wait_s
        with self.cond:
            while True:
                if self._is_down_locked():
                    return "down"
                t, got = self.last_probe
                if got and time.time() - t < PROBE_REUSE_S:
                    return got
                if not self.prober:
                    break
                if time.time() >= end:              # a probe that never returns: answer for it
                    return "down"
                self.cond.wait(0.25)                # another chat is probing: use its answer
                if check_stop:
                    check_stop()                    # Stop works while waiting for another chat's probe
            self.prober = True
            started = time.time()
        got = None                                  # stays None when the probe raised (Stop): record nothing
        try:
            res = probe()
            if str(res).startswith("tls"):          # an untrusted certificate is final: no waiting (S-11)
                raise RuntimeError(_tls_text(res))
            got = "up" if res == "ok" else "auth" if res == "auth" else "down"
        finally:
            with self.cond:
                self.prober = False
                if got is None:
                    pass
                elif got == "down" and self.up_at > started:   # an answer from before it came back: stale
                    got = "up"
                else:
                    self.last_probe = (time.time(), got)
                    if got == "down":
                        self._mark_down()
                        self._failed()              # this probe counts: the next one waits its turn
                    elif got == "up" and self._is_down_locked():
                        self._mark_up()
                self.cond.notify_all()
        return got

    def _failed(self) -> float:
        self.fails += 1
        wait = min(6 * BACKOFF_S, BACKOFF_S * self.fails)
        self.next_probe = time.time() + wait
        self.last_probe = (time.time(), "down")
        if self.down_since:
            self._write_marker()                    # keep the other processes' copy of the backoff current
        return wait

    def _mark_down(self):
        if not self.down_since:
            self.down_since = time.time()
        self.gate.degrade()
        self._write_marker()

    def _mark_up(self):
        self.down_since, self.next_probe, self.fails = 0.0, 0.0, 0
        self.up_at = time.time()
        self.last_probe = (self.up_at, "up")
        self._clear_marker()
        self.cond.notify_all()

    def wait_until_up(self, probe, sleep, emit, budget_s: float, check_stop) -> bool:
        with self.cond:
            self.waiters += 1
            alone = self.waiters == 1
        try:
            return self._wait_until_up(probe, sleep, emit, budget_s, check_stop, alone)
        finally:
            with self.cond:
                self.waiters -= 1

    def _wait_until_up(self, probe, sleep, emit, budget_s, check_stop, alone=False) -> bool:
        """Block until the endpoint answers a probe, for up to budget_s. One caller probes at a time, on a backoff
        shared by every caller (10, 20 ... 60 s); the others wait for its answer. With no budget (recovery off) the
        caller probes once at once, whatever the backoff, so a past outage never blocks the app. True when it is
        up. Stop works throughout (sleep and check_stop raise)."""
        t0, told, first = time.time(), False, True
        while True:
            check_stop()
            with self.cond:
                if not self._is_down_locked():
                    return True
                now = time.time()
                lead = not self.prober and (now >= self.next_probe or (first and (budget_s <= 0 or alone)))
                if not lead and now - t0 >= budget_s:
                    return False
                if lead:
                    self.prober = True
                prober = self.prober
            first = False
            if not lead:                            # another caller is probing, or it is not time yet
                if not told and prober:
                    told = True
                    emit({"type": "status", "text": "The model endpoint isn't answering. Another chat is already "
                                                    "checking it; this one carries on when it is back."})
                elif not told:
                    told = True
                    emit({"type": "status", "text": "The model endpoint isn't answering. Checking again in "
                                                    f"{max(1, int(self.next_probe - time.time()))} s."})
                sleep(min(1.0, max(0.05, budget_s - (time.time() - t0))))
                continue
            wait, got = 0.0, ""
            try:
                got = probe()
                if str(got).startswith("tls"):
                    raise RuntimeError(_tls_text(got))
                if got == "auth":
                    raise RuntimeError("HTTP 401: the endpoint stopped accepting the API key while the task ran.")
                with self.cond:
                    if got == "ok":
                        self._mark_up()
                    else:
                        wait = self._failed()
            finally:
                with self.cond:
                    self.prober = False
                    self.cond.notify_all()
            if got == "ok":
                emit({"type": "status", "text": "The model endpoint is answering again. Picking up where it left off."})
                return True
            if time.time() - t0 + wait < budget_s:
                emit({"type": "status", "text": f"The model endpoint isn't answering ({str(got)[:80]}). Checking "
                                                f"again in {wait:.0f} s, for up to {max(1, int(budget_s // 60))} min. "
                                                "Press Stop to end."})


def _tls_text(res: str) -> str:
    from meshpool_harness.core import refusals as RF
    return ("The endpoint's TLS certificate is not trusted, so it cannot be reached. " + (RF.diagnose(str(res)) or {}).get("note", "Trust its CA or use http:// for a local server.")
            + " (" + str(res)[:160] + ")")


_GATES: dict[str, Gate] = {}
_REG: dict[str, Health] = {}
_REG_LOCK = threading.Lock()


def health(base: str, key: str, model: str = "") -> Health:
    """The Health of one model on one endpoint and key; every model on the key shares one Gate."""
    k = (base or "").rstrip("/") + "|" + (key or "")[-8:]
    with _REG_LOCK:
        g = _GATES.get(k)
        if g is None:
            g = _GATES[k] = Gate(hashlib.sha256(k.encode("utf-8")).hexdigest()[:16], base)
        h = _REG.get(k + "|" + model)
        if h is None:
            h = _REG[k + "|" + model] = Health(g, model)
        return h


def reset():
    """Tests: forget every endpoint's state."""
    with _REG_LOCK:
        _REG.clear()
        _GATES.clear()


def snapshot() -> list[dict]:
    """Every endpoint this process has used: its calls running and waiting, its limit now and its cap, and the models
    found down. Names the endpoint's host only, never the key."""
    with _REG_LOCK:
        gates, healths = list(_GATES.values()), list(_REG.values())
    out = []
    for g in gates:
        out.append({"endpoint": g.host, "inflight": g.inflight, "limit": g.limit, "cap": g.cap,
                    "waiting": g.waiting, "degraded": g.degraded,
                    "down_models": sorted(h.model for h in healths if h.gate is g and h.model and h.is_down())})
    return out


class EndpointDown(RuntimeError):
    """The endpoint is marked down and did not come back in time: the request was not sent."""


def _recover_budget_s() -> float:
    try:
        return float(os.environ.get("MH_RECOVER_MIN") or 30) * 60
    except ValueError:
        return 30 * 60.0


def call(base: str, key: str, model: str, send, *, probe=None, check_stop=None, emit=None, recover_s=None):
    """One model call through the gate and the endpoint's health: wait while the endpoint is marked down (here or by
    another process), take a slot, run send(), give the slot back (ok or failed). A failed send that says 429
    lowers the gate's live limit; one that looks like an outage asks the probe whether the endpoint is up."""
    stop = check_stop or (lambda: None)
    h = health(base, key, model)
    if h.is_down():
        up = False
        if probe is not None and recover_s != 0:       # recover_s=0: fail at once, no probe (side calls)
            def sleep(s):
                end = time.time() + s
                while time.time() < end:
                    time.sleep(min(0.25, max(0.0, end - time.time())))
                    stop()
            up = h.wait_until_up(probe, sleep, emit or (lambda e: None),
                                 recover_s if recover_s is not None else _recover_budget_s(), stop)
        if not up:
            raise EndpointDown("HTTP 0: the model endpoint is down (marked down by another chat or process); "
                               "request not sent")
    slot = h.acquire(stop, emit)
    done = False
    try:
        res = send()
        done = True
        h.release(True, slot)
        return res
    except RuntimeError as e:
        if not done:
            done = True
            text = str(e)
            from meshpool_harness.core.client import classify_error
            kind = classify_error(text)
            if kind != "quota" and ("HTTP 429" in text or re.search(r"too many concurrent", text, re.I)):
                h.gate.throttled()
            h.release(False, slot)
            if kind == "transient" and probe is not None:
                h.check(probe)
        raise
    finally:
        if not done:
            h.release(False, slot)
