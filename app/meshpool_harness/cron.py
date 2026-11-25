"""Five-field cron expressions for scheduled tasks: minute hour day-of-month month day-of-week, local time.

Each field takes *, a number, a range (1-5), a list (1,15), a step (*/10, 0-30/5), and month and day names
(jan, mon). Day 0 and 7 are Sunday. As in cron, when both day fields are restricted, either one matching is enough.
"""
from __future__ import annotations

from datetime import datetime, timedelta

_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_DAYS = {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())}
_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


class CronError(ValueError):
    pass


def _field(text: str, lo: int, hi: int, names: dict) -> set[int]:
    out: set[int] = set()
    for part in text.lower().split(","):
        if not part:
            raise CronError("an empty item in a list")
        rng, _, step = part.partition("/")
        try:
            step_n = int(step) if step else 1
        except ValueError:
            raise CronError(f"{part!r}: the step is not a number")
        if step_n < 1:
            raise CronError(f"{part!r}: the step must be 1 or more")
        if rng == "*":
            a, b = lo, hi
        else:
            x, dash, y = rng.partition("-")
            try:
                a = names[x] if x in names else int(x)
                b = (names[y] if y in names else int(y)) if dash else (hi if step else a)
            except ValueError:
                raise CronError(f"{part!r} is not a number or a name")
        if not (lo <= a <= hi and lo <= b <= hi) or a > b:
            raise CronError(f"{part!r} is outside {lo}-{hi}")
        out.update(range(a, b + 1, step_n))
    return out


def parse(expr: str) -> tuple:
    parts = str(expr or "").split()
    if len(parts) != 5:
        raise CronError("A cron expression has five fields: minute hour day month weekday, like 30 8 * * 1-5")
    mins, hours, dom, months, dow = (_field(p, lo, hi, n) for p, (lo, hi), n in
                                     zip(parts, _RANGES, ({}, {}, {}, _MONTHS, _DAYS)))
    dow = {0 if d == 7 else d for d in dow}
    return mins, hours, dom, months, dow, parts[2] != "*", parts[4] != "*"


def validate(expr: str, min_gap_minutes: int = 15) -> str:
    """The expression, tidied; CronError when it is not valid or fires more often than every min_gap_minutes."""
    mins = parse(expr)[0]
    ms = sorted(mins)
    gaps = [b - a for a, b in zip(ms, ms[1:])] + [60 - ms[-1] + ms[0]]
    if len(ms) > 1 and min(gaps) < min_gap_minutes:
        raise CronError(f"That runs more often than every {min_gap_minutes} minutes.")
    return " ".join(str(expr).split())


def next_after(expr: str, after: datetime) -> datetime | None:
    """The first time after `after` (to the minute) that the expression matches, within about 4 years."""
    mins, hours, dom, months, dow, dom_set, dow_set = parse(expr)
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = t + timedelta(days=366 * 4)
    while t < end:
        if t.month not in months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue
        cron_dow = (t.weekday() + 1) % 7               # Python: Monday 0; cron: Sunday 0
        day_ok = ((t.day in dom) or (cron_dow in dow)) if (dom_set and dow_set) else \
            (t.day in dom if dom_set else True) and (cron_dow in dow if dow_set else True)
        if not day_ok:
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if t.hour not in hours:
            t = t.replace(minute=0) + timedelta(hours=1)
            continue
        if t.minute not in mins:
            t += timedelta(minutes=1)
            continue
        return t
    return None
