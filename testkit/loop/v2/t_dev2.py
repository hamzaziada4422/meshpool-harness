"""Second batch of dev tasks (ids dev2-*): denser specs, nastier data, subtler bugs. All pure standard library."""
import csv
import io
import json
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from common import TASKS, task
from t_tests import mut_task


def data_task(id, prompt, files, must, must_not, answer):
    TASKS.append({"id": f"v2-{id}", "category": "data", "difficulty": "hard", "split": "dev", "mode": "work",
                  "prompt": prompt, "files": files, "ref": {"__answer__": answer},
                  "grade": {"must": must, "must_not": must_not, "max_words": 400}})


def to_csv(header, rows):
    b = io.StringIO()
    w = csv.writer(b, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return b.getvalue()


def num_re(x):
    return r"(?<![\d.,])" + f"{x:,.2f}".replace(",", ",?").replace(".", r"\.") + r"(?!\d)"


# =====================================================================================================================
# ALGORITHMIC
# =====================================================================================================================
LB_SRC = r'''class Leaderboard:
    def __init__(self):
        self._scores = {}

    def set(self, name, score):
        self._scores[name] = score

    def add(self, name, delta):
        self._scores[name] = self._scores.get(name, 0) + delta

    def remove(self, name):
        return self._scores.pop(name, None) is not None

    def _ordered(self):
        return sorted(self._scores.items(), key=lambda kv: (-kv[1], kv[0]))

    def rank(self, name):
        if name not in self._scores:
            return None
        return 1 + sum(1 for s in self._scores.values() if s > self._scores[name])

    def top(self, k):
        return [(self.rank(n), n, s) for n, s in self._ordered()[:k]]

    def around(self, name, n):
        return []

    def __len__(self):
        return len(self._scores)

    def __contains__(self, name):
        return name in self._scores
'''
LB_REF = r'''from bisect import bisect_left, insort


class Leaderboard:
    def __init__(self):
        self._key = {}      # name -> (-score, stamp, name)
        self._keys = []     # sorted keys
        self._tick = 0

    def _put(self, name, score):
        old = self._key.get(name)
        if old is not None:
            if -old[0] == score:
                return
            del self._keys[bisect_left(self._keys, old)]
        self._tick += 1
        key = (-score, self._tick, name)
        self._key[name] = key
        insort(self._keys, key)

    def set(self, name, score):
        self._put(name, score)

    def add(self, name, delta):
        old = self._key.get(name)
        self._put(name, (-old[0] if old else 0) + delta)

    def remove(self, name):
        old = self._key.pop(name, None)
        if old is None:
            return False
        del self._keys[bisect_left(self._keys, old)]
        return True

    def rank(self, name):
        key = self._key.get(name)
        return None if key is None else 1 + bisect_left(self._keys, (key[0],))

    def _slice(self, lo, hi):
        lo = max(lo, 0)
        out, rank, prev = [], None, None
        for i in range(lo, min(hi, len(self._keys))):
            sc, _, n = self._keys[i]
            if sc != prev or rank is None:
                rank = 1 + bisect_left(self._keys, (sc,), 0, i + 1)
                prev = sc
            out.append((rank, n, -sc))
        return out

    def top(self, k):
        return self._slice(0, k) if k > 0 else []

    def around(self, name, n):
        key = self._key.get(name)
        if key is None:
            return []
        pos = bisect_left(self._keys, key)
        return self._slice(pos - n, pos + n + 1)

    def __len__(self):
        return len(self._key)

    def __contains__(self, name):
        return name in self._key
'''
LB_TEST = r'''import random, time
from lb import Leaderboard


class Oracle:
    def __init__(self):
        self.d, self.t = {}, 0

    def put(self, n, s):
        if n in self.d and self.d[n][0] == s:
            return
        self.t += 1
        self.d[n] = (s, self.t)

    def order(self):
        return sorted(self.d.items(), key=lambda kv: (-kv[1][0], kv[1][1], kv[0]))

    def rank(self, n):
        return None if n not in self.d else 1 + sum(1 for v in self.d.values() if v[0] > self.d[n][0])

    def rows(self, items):
        return [(self.rank(n), n, v[0]) for n, v in items]


lb = Leaderboard()
lb.set("a", 10); lb.set("b", 10); lb.set("c", 10)
assert lb.top(5) == [(1, "a", 10), (1, "b", 10), (1, "c", 10)]
lb.set("a", 10)                       # same score: keeps its standing
assert lb.top(1) == [(1, "a", 10)]
lb.set("a", 11); lb.set("a", 10)      # changed and changed back: now the newest at 10
assert [n for _, n, _ in lb.top(3)] == ["b", "c", "a"]
lb.add("d", 0)                        # creates with 0
assert "d" in lb and lb.rank("d") == 4 and len(lb) == 4
lb.add("b", 0)                        # no-op on existing
assert [n for _, n, _ in lb.top(3)] == ["b", "c", "a"]
assert lb.remove("b") is True and lb.remove("b") is False and lb.rank("b") is None
lb.set("b", 10)                       # re-added: fresh stamp
assert [n for _, n, _ in lb.top(4)] == ["c", "a", "b", "d"]
assert lb.top(0) == [] and lb.top(-3) == [] and len(lb.top(99)) == 4
lb.set("x", 5); lb.set("y", 5); lb.set("z", 7)
assert lb.top(7) == [(1, "c", 10), (1, "a", 10), (1, "b", 10), (4, "z", 7), (5, "x", 5), (5, "y", 5), (7, "d", 0)]
assert lb.rank("y") == 5 and lb.rank("nobody") is None
assert lb.around("z", 1) == [(1, "b", 10), (4, "z", 7), (5, "x", 5)]
assert lb.around("c", 2) == [(1, "c", 10), (1, "a", 10), (1, "b", 10)]
assert lb.around("d", 1) == [(5, "y", 5), (7, "d", 0)]
assert lb.around("nobody", 3) == [] and lb.around("z", 0) == [(4, "z", 7)]

rnd = random.Random(3)
for round_ in range(40):
    lb, o = Leaderboard(), Oracle()
    names = [f"n{i}" for i in range(rnd.randint(1, 12))]
    for _ in range(rnd.randint(1, 120)):
        n, op = rnd.choice(names), rnd.random()
        if op < 0.45:
            s = rnd.randint(-3, 3); lb.set(n, s); o.put(n, s)
        elif op < 0.75:
            d = rnd.randint(-2, 2); lb.add(n, d); o.put(n, (o.d[n][0] if n in o.d else 0) + d)
        else:
            assert lb.remove(n) == (n in o.d); o.d.pop(n, None)
        if rnd.random() < 0.5:
            k = rnd.randint(-1, 15)
            assert lb.top(k) == o.rows(o.order()[:max(k, 0)]), (round_, k)
            q = rnd.choice(names)
            assert lb.rank(q) == o.rank(q)
            m = rnd.randint(0, 3)
            ordd = o.order(); idx = [i for i, (nm, _) in enumerate(ordd) if nm == q]
            want = o.rows(ordd[max(idx[0] - m, 0):idx[0] + m + 1]) if idx else []
            assert lb.around(q, m) == want, (round_, q, m)
    assert len(lb) == len(o.d)

rnd = random.Random(99)
lb = Leaderboard()
t = time.perf_counter()
for i in range(90000):
    n = f"p{rnd.randrange(20000)}"
    op = rnd.random()
    if op < 0.5:
        lb.set(n, rnd.randint(0, 3000))
    elif op < 0.85:
        lb.add(n, rnd.randint(-50, 50))
    else:
        lb.remove(n)
tot = 0
for i in range(3000):
    r = lb.rank(f"p{rnd.randrange(20000)}")
    tot += r or 0
for i in range(300):
    lb.top(25)
    lb.around(f"p{rnd.randrange(20000)}", 10)
dt = time.perf_counter() - t
assert len(lb) > 5000 and tot > 0
assert dt < 6, dt
print("ok")
'''
task("dev2-alg-leaderboard", "algorithmic",
     "Implement Leaderboard in lb.py (a stub that is only roughly right is there now). Scores are ints.\n"
     "- set(name, score): create or overwrite. add(name, delta): like set(name, current + delta), a missing name counts as 0 "
     "and is created even for delta 0.\n"
     "- Every entry carries a stamp: it gets a fresh, increasing stamp when it is created and whenever its score actually "
     "changes. set/add that leave the score unchanged do not touch the stamp. remove(name) returns True/False; adding the "
     "name again later gives it a fresh stamp.\n"
     "- Order: score descending, then older stamp first, then name ascending.\n"
     "- rank(name): competition ranking = 1 + number of entries with a strictly higher score (ties share a rank, the next "
     "rank skips); None if absent.\n"
     "- top(k): list of (rank, name, score) for the first k entries in order; [] for k <= 0; all entries if k is larger.\n"
     "- around(name, n): the entries from n positions before to n positions after name in order (clipped to the ends), as "
     "(rank, name, score) tuples; [] if name is absent. Ranks are always the true competition ranks, not positions in the slice.\n"
     "- len(lb) and `name in lb` work. It must stay fast with ~20k names and ~90k operations followed by thousands of "
     "rank/top/around queries.",
     {"lb.py": LB_SRC}, {"hidden_t.py": LB_TEST}, "python3 hidden_t.py", {"lb.py": LB_REF}, split="dev", timeout=120)

IV_SRC = r'''class IntervalSet:
    """Set of integers stored as half-open [start, end) intervals."""

    def __init__(self):
        self._iv = []

    def add(self, start, end):
        self._iv.append((start, end))
        self._iv.sort()
        merged = []
        for s, e in self._iv:
            if merged and s < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        self._iv = merged

    def remove(self, start, end):
        out = []
        for s, e in self._iv:
            if e <= start or s >= end:
                out.append((s, e))
            else:
                if s < start:
                    out.append((s, start))
                if e > end:
                    out.append((end, e))
        self._iv = out

    def contains(self, x):
        return any(s <= x < e for s, e in self._iv)

    def covered(self, a, b):
        return sum(max(0, min(e, b) - max(s, a)) for s, e in self._iv)

    def gaps(self, a, b):
        return []

    def total(self):
        return sum(e - s for s, e in self._iv)

    def intervals(self):
        return list(self._iv)
'''
IV_REF = r'''from bisect import bisect_left, bisect_right


class IntervalSet:
    """Set of integers stored as half-open [start, end) intervals."""

    def __init__(self):
        self._s = []
        self._e = []

    def add(self, start, end):
        if start >= end:
            return
        lo = bisect_left(self._e, start)       # first interval ending at or after start (touching merges)
        hi = bisect_right(self._s, end)        # intervals starting at or before end
        if lo < hi:
            start = min(start, self._s[lo])
            end = max(end, self._e[hi - 1])
        self._s[lo:hi] = [start]
        self._e[lo:hi] = [end]

    def remove(self, start, end):
        if start >= end:
            return
        lo = bisect_right(self._e, start)      # intervals ending after start
        hi = bisect_left(self._s, end)         # intervals starting before end
        if lo >= hi:
            return
        ns, ne = [], []
        if self._s[lo] < start:
            ns.append(self._s[lo]); ne.append(start)
        if self._e[hi - 1] > end:
            ns.append(end); ne.append(self._e[hi - 1])
        self._s[lo:hi] = ns
        self._e[lo:hi] = ne

    def contains(self, x):
        i = bisect_right(self._s, x) - 1
        return i >= 0 and x < self._e[i]

    def covered(self, a, b):
        if a >= b:
            return 0
        lo, hi = bisect_right(self._e, a), bisect_left(self._s, b)
        return sum(min(self._e[i], b) - max(self._s[i], a) for i in range(lo, hi))

    def gaps(self, a, b):
        if a >= b:
            return []
        out, cur = [], a
        for i in range(bisect_right(self._e, a), bisect_left(self._s, b)):
            if self._s[i] > cur:
                out.append((cur, self._s[i]))
            cur = max(cur, self._e[i])
        if cur < b:
            out.append((cur, b))
        return out

    def total(self):
        return sum(e - s for s, e in zip(self._s, self._e))

    def intervals(self):
        return list(zip(self._s, self._e))
'''
IV_TEST = r'''import random, time
from ivset import IntervalSet


def runs(points):
    out, pts = [], sorted(points)
    for p in pts:
        if out and out[-1][1] == p:
            out[-1] = (out[-1][0], p + 1)
        else:
            out.append((p, p + 1))
    return out


s = IntervalSet()
s.add(1, 3); s.add(3, 5)
assert s.intervals() == [(1, 5)]                 # touching intervals merge
s.add(7, 7); s.add(9, 8); s.add(-2, -2)
assert s.intervals() == [(1, 5)]                 # empty / inverted ignored
s.add(10, 12)
assert s.intervals() == [(1, 5), (10, 12)] and s.total() == 6
s.remove(2, 3)
assert s.intervals() == [(1, 2), (3, 5), (10, 12)]
s.remove(5, 10)
assert s.intervals() == [(1, 2), (3, 5), (10, 12)]
s.remove(4, 4); s.remove(6, 2)
assert s.intervals() == [(1, 2), (3, 5), (10, 12)]
assert s.gaps(0, 13) == [(0, 1), (2, 3), (5, 10), (12, 13)]
assert s.gaps(3, 5) == [] and s.gaps(5, 5) == [] and s.gaps(8, 2) == []
assert s.gaps(4, 11) == [(5, 10)]
assert s.covered(0, 100) == 5 and s.covered(4, 11) == 2 and s.covered(5, 5) == 0 and s.covered(9, 2) == 0
assert s.contains(1) and not s.contains(2) and s.contains(11) and not s.contains(12) and not s.contains(-5)

rnd = random.Random(8)
for rd in range(300):
    s, pts = IntervalSet(), set()
    for _ in range(rnd.randint(1, 25)):
        a, b = rnd.randint(-5, 30), rnd.randint(-5, 30)
        if rnd.random() < 0.55:
            s.add(a, b); pts |= set(range(a, b))
        else:
            s.remove(a, b); pts -= set(range(a, b))
        assert s.intervals() == runs(pts), (rd, s.intervals(), runs(pts))
    assert s.total() == len(pts)
    for _ in range(10):
        a, b = rnd.randint(-8, 33), rnd.randint(-8, 33)
        assert s.covered(a, b) == sum(1 for p in range(a, b) if p in pts)
        assert s.gaps(a, b) == runs(set(range(a, b)) - pts)
        assert s.contains(a) == (a in pts)

rnd = random.Random(21)
s = IntervalSet()
t = time.perf_counter()
for i in range(70000):
    a = rnd.randrange(10 ** 9)
    if rnd.random() < 0.7:
        s.add(a, a + rnd.randint(1, 3000))
    else:
        s.remove(a, a + rnd.randint(1, 6000))
n = len(s.intervals())
cov = sum(s.covered(rnd.randrange(10 ** 9 // 2), 10 ** 9) for _ in range(40))
hits = sum(s.contains(rnd.randrange(10 ** 9)) for _ in range(30000))
g = len(s.gaps(0, 10 ** 9))
dt = time.perf_counter() - t
assert n > 5000 and g >= n - 1 and cov > 0
assert dt < 6, dt
print("ok")
'''
task("dev2-alg-intervalset", "algorithmic",
     "Finish IntervalSet in ivset.py (the current version is slow and wrong in places). It stores sets of integers as "
     "half-open [start, end) ranges.\n"
     "- add(s, e) and remove(s, e) silently ignore empty or inverted ranges (s >= e).\n"
     "- add merges with overlapping AND merely touching ranges ([1,3) + [3,5) is one range [1,5)); intervals() always "
     "returns sorted, disjoint, non-touching (start, end) tuples.\n"
     "- remove can split a range in two or trim it; removing something absent changes nothing.\n"
     "- contains(x): s <= x < e for some range. covered(a, b): how many members lie in [a, b) (0 if a >= b). "
     "total(): size of the whole set.\n"
     "- gaps(a, b): sorted (start, end) pieces of [a, b) NOT in the set, as maximal runs; [] if a >= b.\n"
     "It must handle ~70k random add/remove calls over a 10^9 range (tens of thousands of ranges alive), then many "
     "queries, in a few seconds.",
     {"ivset.py": IV_SRC}, {"hidden_t.py": IV_TEST}, "python3 hidden_t.py", {"ivset.py": IV_REF}, split="dev", timeout=120)

# =====================================================================================================================
# DATA (answered in the reply)
# =====================================================================================================================
# 1: subscription MRR with versioned rows, id variants, mixed currencies/periods/date formats, blank amounts
r = random.Random(2101)
PLANS = {"basic": 12.0, "pro": 49.0, "team": 120.0}
RATES = {"USD": 1.0, "EUR": 1.10, "GBP": 1.30, "JPY": 0.0067}
CUTOFF = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"]


def sub_variant(n):
    return r.choice([f"S{n:04d}", f"s{n}", f" S{n:03d} ", f"S{n}", f"s{n:05d}\t"])


def norm_id(s):
    return int(s.strip().lower().lstrip("s"))


sub_rows, final = [], {}          # final: id -> (moment, row)
all_rows_norm = []
for n in range(1, 131):
    plan = r.choice(list(PLANS))
    cur = r.choice(["USD", "USD", "EUR", "GBP", "JPY"])
    period = r.choice(["monthly", "monthly", "annual"])
    if cur == "JPY":
        base = r.choice([1800, 6500, 14000]) * (12 if period == "annual" else 1)
    else:
        base = round(PLANS[plan] * r.choice([0.9, 1, 1, 1.5]) * (11 if period == "annual" else 1), 2)
    t = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=r.randint(0, 40), hours=r.randint(0, 23))
    for k in range(r.choice([1, 1, 2, 3])):
        t += timedelta(days=r.randint(9, 70), hours=r.randint(1, 23), minutes=r.randint(1, 59))
        status = r.choice(["Active", "Active", "active ", "ACTIVE", "cancelled", "Cancelled", "trial", "paused"])
        amt = base if r.random() > 0.1 else None
        style = r.choice(["iso_date", "dmy", "iso_z"])
        if style == "iso_date":
            ts = t.strftime("%Y-%m-%d")
            moment = datetime(t.year, t.month, t.day, 12, tzinfo=timezone.utc)
        elif style == "dmy":
            ts = f"{t.day:02d} {MONTHS[t.month - 1]} {t.year}"
            moment = datetime(t.year, t.month, t.day, 12, tzinfo=timezone.utc)
        else:
            ts = t.strftime("%Y-%m-%dT%H:%M:%SZ")
            moment = t
        if k == 0:
            status = "Active" if status == "paused" else status
        shown = "" if amt is None else (f"{amt:,.2f}" if amt >= 1000 else f"{amt:.2f}" if cur != "JPY" else str(int(amt)))
        sub_rows.append([sub_variant(n), plan, shown, cur, r.choice([period, period.upper(), period.title()]),
                         status, ts])
        # stored moment: compare by calendar day for date-only rows, by instant for iso_z rows
        all_rows_norm.append((n, moment, t, plan, amt, cur, period, status.strip().lower(), len(sub_rows) - 1))
r.shuffle(sub_rows)
# recompute with the shuffled order irrelevant: truth uses the generated records, not file order
by = {}
for n, moment, t, plan, amt, cur, period, status, idx in all_rows_norm:
    if moment <= CUTOFF:
        if n not in by or moment > by[n][0]:
            by[n] = (moment, plan, amt, cur, period, status)
truth = 0.0
for n, (moment, plan, amt, cur, period, status) in by.items():
    if status != "active":
        continue
    mrr = PLANS[plan] if amt is None else amt * RATES[cur] / (12 if period == "annual" else 1)
    truth += mrr
truth = round(truth, 2)
naive_all = 0.0           # latest row ignoring the cutoff
by2 = {}
for n, moment, t, plan, amt, cur, period, status, idx in all_rows_norm:
    if n not in by2 or moment > by2[n][0]:
        by2[n] = (moment, plan, amt, cur, period, status)
for n, (moment, plan, amt, cur, period, status) in by2.items():
    if status == "active":
        naive_all += PLANS[plan] if amt is None else amt * RATES[cur] / (12 if period == "annual" else 1)
naive_rows = 0.0          # every active row counted, no versioning
for n, moment, t, plan, amt, cur, period, status, idx in all_rows_norm:
    if status == "active" and moment <= CUTOFF:
        naive_rows += PLANS[plan] if amt is None else amt * RATES[cur] / (12 if period == "annual" else 1)
wrong = [round(naive_all, 2), round(naive_rows, 2)]
data_task("dev2-data-mrr",
          "From subs.csv, plans.csv and rates.csv: what is our total monthly recurring revenue in USD as of the end of "
          "2026-06-30 (UTC)? Each subscription appears in several rows (one per change); the row with the latest "
          "updated_at that is not after the cutoff is the subscription's state then, and only subscriptions whose "
          "state is active count. sub_id is written inconsistently (case, spaces, leading zeros) and means the same "
          "subscription. Annual amounts are per year (divide by 12). rates.csv gives USD per unit of currency. "
          "plans.csv gives the monthly USD list price, used when the state row has no amount. Give the total to the cent.",
          {"subs.csv": to_csv(["sub_id", "plan", "amount", "currency", "period", "status", "updated_at"], sub_rows),
           "plans.csv": to_csv(["plan", "list_price_usd_monthly"], [[k, v] for k, v in PLANS.items()]),
           "rates.csv": to_csv(["currency", "usd_per_unit"], [[k, v] for k, v in RATES.items()])},
          [num_re(truth)], [num_re(w) for w in wrong if w != truth], f"Total MRR: ${truth:,.2f}")

# 2: purchase events on a New York DST-change day; mixed timestamp encodings, duplicates, test accounts, currencies
r = random.Random(2102)
NY, CHI = ZoneInfo("America/New_York"), ZoneInfo("America/Chicago")
FX = {"USD": 1.0, "EUR": 1.09, "CAD": 0.74}
day_start = datetime(2026, 3, 8, tzinfo=NY).astimezone(timezone.utc)
day_end = datetime(2026, 3, 9, tzinfo=NY).astimezone(timezone.utc)
events, truth_rows, seen_ids = [], {}, set()
t0 = datetime(2026, 3, 7, 12, tzinfo=timezone.utc)
users = [f"user{i}@mail.example" for i in range(60)]
for i in range(700):
    inst = t0 + timedelta(seconds=r.randint(0, 3 * 86400))
    eid = f"E{i:04d}"
    typ = r.choice(["purchase", "purchase", "purchase", "view", "refund"])
    user = r.choice(users) if r.random() > 0.08 else f"  QA{r.randint(1, 4)}@MeshPool.test "
    cur = r.choice(["USD", "USD", "EUR", "CAD"])
    cents = r.randint(300, 40000)
    fmt = r.choice(["z", "off", "s", "ms", "naive"])

    def enc(inst, fmt):
        if fmt == "z":
            return inst.strftime("%Y-%m-%dT%H:%M:%SZ")
        if fmt == "off":
            tz = timezone(timedelta(hours=r.choice([-8, 5.5, 2, -4])))
            return inst.astimezone(tz).isoformat()
        if fmt == "s":
            return int(inst.timestamp())
        if fmt == "ms":
            return str(int(inst.timestamp()) * 1000)
        return inst.astimezone(CHI).strftime("%Y-%m-%d %H:%M:%S")
    rec = {"event_id": eid, "user": user, "ts": enc(inst, fmt), "type": typ, "currency": cur}
    if r.random() < 0.5:
        rec["amount_cents"] = cents
    else:
        rec["amount"] = f"{cents / 100:.2f}"
    events.append(rec)
    if typ == "purchase" and "@meshpool.test" not in user.strip().lower() and day_start <= inst < day_end:
        truth_rows[eid] = cents / 100 * FX[cur]
    if r.random() < 0.09:                      # same event again, other encoding of the same instant
        dup = dict(rec)
        dup["ts"] = enc(inst, r.choice([f for f in ["z", "s", "ms", "naive"] if f != fmt]))
        events.append(dup)
r.shuffle(events)
count = len(truth_rows)
total = round(sum(truth_rows.values()), 2)
# plausible wrong answers
w_total = []
nodup = 0.0
for e in events:
    inst_raw = e["ts"]
    if isinstance(inst_raw, int) or str(inst_raw).isdigit():
        v = int(inst_raw)
        inst = datetime.fromtimestamp(v / 1000 if v > 10 ** 11 else v, timezone.utc)
    elif " " in str(inst_raw) and "T" not in str(inst_raw):
        inst = datetime.strptime(inst_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHI).astimezone(timezone.utc)
    else:
        inst = datetime.fromisoformat(inst_raw.replace("Z", "+00:00"))
    if e["type"] == "purchase" and "@meshpool.test" not in e["user"].strip().lower() and day_start <= inst < day_end:
        c = e["amount_cents"] if "amount_cents" in e else round(float(e["amount"]) * 100)
        nodup += c / 100 * FX[e["currency"]]
w_total.append(round(nodup, 2))                       # duplicates counted
data_task("dev2-data-dst-day",
          "events.jsonl is our event log (one JSON object per line). Using notes.txt for the conventions, how many "
          "distinct purchases were made on 2026-03-08 in New York local time, and what is their total revenue in USD "
          "(to the cent)?",
          {"events.jsonl": "\n".join(json.dumps(e) for e in events) + "\n",
           "notes.txt": "Conventions\n- event_id identifies an event; the same event can be logged more than once "
                        "(possibly with the timestamp written differently). Count it once.\n"
                        "- ts is one of: ISO 8601 with Z or numeric offset; Unix seconds (int or string); Unix "
                        "milliseconds (13-digit string); or 'YYYY-MM-DD HH:MM:SS' without zone, which is the "
                        "warehouse's local time in America/Chicago.\n"
                        "- Purchases are events with type 'purchase'. Ignore purchases by internal QA users (email "
                        "domain meshpool.test, any capitalisation, stray spaces).\n"
                        "- Amount is either amount_cents (integer cents) or amount (a decimal string in major units), "
                        "in the event's currency. USD per unit: EUR 1.09, CAD 0.74.\n"
                        "- 'Local day' means the calendar day in the reporting zone, America/New_York.\n"},
          [num_re(total), rf"(?<![\d.,]){count}(?![\d,]|\.\d)"], [num_re(w) for w in w_total if w != total],
          f"{count} purchases, total ${total:,.2f}")

# =====================================================================================================================
# DEBUGGING
# =====================================================================================================================
CLIENT_SRC = r'''import copy

DEFAULT_HEADERS = {"Accept": "application/json", "User-Agent": "meshpool/1"}


class Client:
    def __init__(self, base_url, headers=DEFAULT_HEADERS, retries=None):
        self.base_url = base_url.rstrip("/")
        self.headers = headers
        self.retries = retries or {"max": 3, "codes": [502, 503]}

    def with_header(self, name, value):
        c = copy.copy(self)
        c.headers[name] = value
        return c

    def with_retries(self, **changes):
        c = copy.copy(self)
        c.retries.update(changes)
        return c

    def request_headers(self, extra=None):
        h = self.headers
        h.update(extra or {})
        return h

    def should_retry(self, status, attempt):
        return attempt < self.retries["max"] and status in self.retries["codes"]
'''
CLIENT_REF = r'''import copy

DEFAULT_HEADERS = {"Accept": "application/json", "User-Agent": "meshpool/1"}


class Client:
    def __init__(self, base_url, headers=None, retries=None):
        self.base_url = base_url.rstrip("/")
        self.headers = dict(DEFAULT_HEADERS if headers is None else headers)
        self.retries = copy.deepcopy(retries) if retries else {"max": 3, "codes": [502, 503]}

    def _clone(self):
        c = copy.copy(self)
        c.headers = dict(self.headers)
        c.retries = copy.deepcopy(self.retries)
        return c

    def with_header(self, name, value):
        c = self._clone()
        c.headers[name] = value
        return c

    def with_retries(self, **changes):
        c = self._clone()
        c.retries.update(copy.deepcopy(changes))
        return c

    def request_headers(self, extra=None):
        h = dict(self.headers)
        h.update(extra or {})
        return h

    def should_retry(self, status, attempt):
        return attempt < self.retries["max"] and status in self.retries["codes"]
'''
TENANTS_SRC = r'''from client import Client

BASE = Client("https://api.example.test/")


def client_for(tenant, token):
    return BASE.with_header("X-Tenant", tenant).with_header("Authorization", "Bearer " + token)


def flaky_client_for(tenant, token):
    return client_for(tenant, token).with_retries(max=6, codes=[500, 502, 503])
'''
CLIENT_TEST = r'''import client
from client import Client, DEFAULT_HEADERS
import tenants

snap = dict(DEFAULT_HEADERS)
a = tenants.client_for("acme", "tok-a")
b = tenants.client_for("globex", "tok-b")
assert a.headers["X-Tenant"] == "acme" and a.headers["Authorization"] == "Bearer tok-a"
assert b.headers["X-Tenant"] == "globex" and b.headers["Authorization"] == "Bearer tok-b"
assert "X-Tenant" not in tenants.BASE.headers and "Authorization" not in tenants.BASE.headers
assert DEFAULT_HEADERS == snap and "Authorization" not in Client("http://x").headers

f = tenants.flaky_client_for("acme", "t")
assert f.retries == {"max": 6, "codes": [500, 502, 503]}
assert a.retries == {"max": 3, "codes": [502, 503]} and tenants.BASE.retries == {"max": 3, "codes": [502, 503]}
assert a.should_retry(503, 2) and not a.should_retry(500, 0) and f.should_retry(500, 5) and not f.should_retry(500, 6)

h = a.request_headers({"X-Trace": "1"})
assert h["X-Trace"] == "1" and h["X-Tenant"] == "acme" and h["Accept"] == "application/json"
assert "X-Trace" not in a.request_headers() and "X-Trace" not in a.headers
h["X-Evil"] = "1"
assert "X-Evil" not in a.headers and "X-Evil" not in a.request_headers()

mine = {"Accept": "text/plain"}
c = Client("http://x/", headers=mine, retries={"max": 1, "codes": [429]})
c2 = c.with_header("K", "v").with_retries(max=9)
assert mine == {"Accept": "text/plain"}
mine["Accept"] = "changed"
assert c.headers["Accept"] == "text/plain"
assert c.retries == {"max": 1, "codes": [429]} and c2.retries == {"max": 9, "codes": [429]}
c3 = c.with_retries(codes=[1, 2])
c3.retries["codes"].append(3)
assert c.retries["codes"] == [429] and c2.retries["codes"] == [429]
chain = [Client("http://y")]
for i in range(3):
    chain.append(chain[-1].with_header(f"H{i}", str(i)))
assert [len(x.headers) for x in chain] == [2, 3, 4, 5]
d1, d2 = Client("http://p"), Client("http://q")
d1.headers["X-Mine"] = "1"
assert "X-Mine" not in d2.headers and "X-Mine" not in DEFAULT_HEADERS
assert c.base_url == "http://x"
print("ok")
'''
task("dev2-dbg-tenant-headers", "debugging",
     "In production, requests for one tenant sometimes go out carrying another tenant's Authorization header, and "
     "after we made the flaky tenants' clients retry on 500s, the retry behaviour of unrelated tenants changed too. "
     "It only shows up after several clients have been created in the same process. Find the root cause(s) and fix "
     "them without changing the public API of client.py or tenants.py.",
     {"client.py": CLIENT_SRC, "tenants.py": TENANTS_SRC}, {"hidden_t.py": CLIENT_TEST}, "python3 hidden_t.py",
     {"client.py": CLIENT_REF}, split="dev")

SCHED_SRC = r'''from datetime import timedelta, timezone
from zoneinfo import ZoneInfo


def next_run(after, tz_name, hh, mm):
    """Next daily run strictly after `after` (an aware datetime), at local wall-clock hh:mm in tz_name.
    Returned as an aware UTC datetime."""
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    tz = ZoneInfo(tz_name)
    local = after.astimezone(tz)
    today = local.replace(hour=hh, minute=mm, second=0, microsecond=0).astimezone(timezone.utc)
    nxt = today
    while nxt <= after:
        nxt += timedelta(days=1)
    return nxt
'''
SCHED_JOBS = r'''from schedule import next_run


def upcoming(after, tz_name, hh, mm, n):
    """The next n run times (UTC), each strictly after the previous."""
    out = []
    cur = after
    for _ in range(n):
        cur = next_run(cur, tz_name, hh, mm)
        out.append(cur)
    return out
'''
SCHED_REF = r'''from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def next_run(after, tz_name, hh, mm):
    """Next daily run strictly after `after` (an aware datetime), at local wall-clock hh:mm in tz_name.
    Returned as an aware UTC datetime."""
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    tz = ZoneInfo(tz_name)
    day = after.astimezone(tz).date()
    for _ in range(3):
        cand = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz).astimezone(timezone.utc)
        if cand > after:
            return cand
        day += timedelta(days=1)
'''
_u = lambda *a: datetime(*a, tzinfo=timezone.utc)   # noqa: E731
_exp = {}
_ns = {}
exec(SCHED_REF, _ns)
_nr = _ns["next_run"]
_cases = [
    ("America/New_York", 2, 30, _u(2026, 3, 7, 20)), ("America/New_York", 2, 30, _u(2026, 3, 8, 10)),
    ("America/New_York", 2, 30, _u(2026, 3, 8, 7, 30)), ("America/New_York", 1, 30, _u(2026, 11, 1, 4)),
    ("America/New_York", 1, 30, _u(2026, 11, 1, 5, 30)), ("America/New_York", 1, 30, _u(2026, 11, 1, 6, 30)),
    ("America/New_York", 1, 30, _u(2026, 11, 1, 7)), ("Australia/Lord_Howe", 1, 45, _u(2026, 4, 4, 10)),
    ("Australia/Lord_Howe", 1, 45, _u(2026, 4, 4, 14, 45)), ("Australia/Lord_Howe", 2, 15, _u(2026, 10, 3, 1)),
    ("Europe/London", 1, 30, _u(2026, 3, 28, 12)), ("Europe/London", 9, 0, _u(2026, 3, 28, 8, 30)),
    ("Asia/Kolkata", 0, 0, _u(2026, 5, 5, 18, 30)), ("UTC", 23, 59, _u(2026, 12, 31, 23, 59)),
]
_exp = [[tz, hh, mm, a.isoformat(), _nr(a, tz, hh, mm).isoformat()] for tz, hh, mm, a in _cases]
SCHED_TEST = r'''import random, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from schedule import next_run
from jobs import upcoming

CASES = ''' + json.dumps(_exp) + r'''
for tz, hh, mm, a, want in CASES:
    got = next_run(datetime.fromisoformat(a), tz, hh, mm)
    assert got.isoformat() == want and got.utcoffset() == timedelta(0), (tz, hh, mm, a, got.isoformat(), want)

# spelled out: the skipped hour fires at the shifted time once; the repeated hour fires only the first time
assert next_run(datetime(2026, 3, 7, 20, tzinfo=timezone.utc), "America/New_York", 2, 30) == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
assert next_run(datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc), "America/New_York", 2, 30) == datetime(2026, 3, 9, 6, 30, tzinfo=timezone.utc)
assert next_run(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), "America/New_York", 1, 30) == datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)
ups = upcoming(datetime(2026, 11, 1, 3, tzinfo=timezone.utc), "America/New_York", 1, 30, 3)
assert [u.isoformat() for u in ups] == ["2026-11-01T05:30:00+00:00", "2026-11-02T06:30:00+00:00", "2026-11-03T06:30:00+00:00"]

# property check on ordinary wall-clock times against a minute-by-minute scan
rnd = random.Random(4)
zones = ["America/New_York", "Europe/London", "Australia/Lord_Howe", "America/Sao_Paulo", "Asia/Tokyo", "Pacific/Auckland"]
for _ in range(120):
    tz = rnd.choice(zones); z = ZoneInfo(tz)
    hh, mm = rnd.choice([(9, 15), (12, 0), (15, 40), (23, 5)])
    a = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=rnd.randrange(365 * 1440))
    got = next_run(a, tz, hh, mm)
    assert got > a and got.astimezone(z).hour == hh and got.astimezone(z).minute == mm, (tz, a, got)
    t = a.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while t < got:
        l = t.astimezone(z)
        assert not (l.hour == hh and l.minute == mm), (tz, a, got, t)
        t += timedelta(minutes=1)
try:
    next_run(datetime(2026, 1, 1), "UTC", 1, 1)
except ValueError:
    pass
else:
    raise AssertionError("naive datetime accepted")
print("ok")
'''
task("dev2-dbg-dst-schedule", "debugging",
     "Our daily 'digest' job (02:30 America/New_York) runs at the wrong local time for weeks after a clock change, "
     "and last spring a Lord Howe Island customer's 01:45 job fired at an odd time right after their change. "
     "jobs.upcoming() lists the next run times. Fix the scheduling so a job set for local wall-clock hh:mm runs at that "
     "wall-clock time every day. When that time doesn't exist that day (clocks jump forward) it runs once at the "
     "equivalent shifted instant (pre-change offset applied); when it occurs twice (clocks go back) it runs once, at "
     "the first occurrence. Runs are strictly after `after`.",
     {"schedule.py": SCHED_SRC, "jobs.py": SCHED_JOBS}, {"hidden_t.py": SCHED_TEST}, "python3 hidden_t.py",
     {"schedule.py": SCHED_REF}, split="dev")

# =====================================================================================================================
# MULTI-FILE
# =====================================================================================================================
MF1_FILES = {
    "store.py": r'''class UnknownSku(KeyError):
    pass


class OutOfStock(Exception):
    pass


class Store:
    def __init__(self, stock):
        self.stock = dict(stock)

    def reserve(self, sku, qty):
        if qty <= 0:
            raise ValueError("qty must be positive")
        if sku not in self.stock:
            raise UnknownSku(sku)
        if self.stock[sku] < qty:
            raise OutOfStock(sku)
        self.stock[sku] -= qty

    def release(self, sku, qty):
        self.stock[sku] += qty
''',
    "wallet.py": r'''class InsufficientFunds(Exception):
    pass


class Wallet:
    def __init__(self, balance):
        self.balance = balance

    def charge(self, cents):
        if cents > self.balance:
            raise InsufficientFunds(cents)
        self.balance -= cents

    def refund(self, cents):
        self.balance += cents
''',
    "ledger.py": r'''class Ledger:
    def __init__(self):
        self.entries = []

    def record(self, kind, **data):
        entry = {"seq": len(self.entries) + 1, "kind": kind, **data}
        self.entries.append(entry)
        return entry
''',
    "orders.py": r'''class OrderService:
    def __init__(self, store, wallet, ledger):
        self.store = store
        self.wallet = wallet
        self.ledger = ledger
        self.listeners = []

    def place_order(self, sku, qty, unit_price):
        self.store.reserve(sku, qty)
        self.wallet.charge(qty * unit_price)
        entry = self.ledger.record("order", sku=sku, qty=qty, total=qty * unit_price, batch=None)
        for fn in self.listeners:
            fn(entry)
        return entry
''',
}
MF1_REF = {
    "ledger.py": r'''class Ledger:
    def __init__(self):
        self.entries = []
        self._batches = 0

    def next_batch(self):
        return self._batches + 1

    def commit_batch(self):
        self._batches += 1

    def record(self, kind, **data):
        entry = {"seq": len(self.entries) + 1, "kind": kind, **data}
        self.entries.append(entry)
        return entry
''',
    "orders.py": r'''from wallet import InsufficientFunds


class BulkError(Exception):
    def __init__(self, index, cause):
        super().__init__(f"bulk order failed at {index}: {cause!r}")
        self.index = index
        self.cause = cause


class OrderService:
    def __init__(self, store, wallet, ledger):
        self.store = store
        self.wallet = wallet
        self.ledger = ledger
        self.listeners = []
        self.listener_errors = []

    def _reserve_all(self, lines, wrap):
        done = []
        try:
            for i, (sku, qty, _price) in enumerate(lines):
                try:
                    self.store.reserve(sku, qty)
                except Exception as exc:
                    raise (BulkError(i, exc) if wrap else exc)
                done.append((sku, qty))
        except BaseException:
            for sku, qty in reversed(done):
                self.store.release(sku, qty)
            raise
        return done

    def _notify(self, entries):
        for entry in entries:
            for fn in list(self.listeners):
                try:
                    fn(entry)
                except Exception as exc:
                    self.listener_errors.append((entry, exc))

    def place_order(self, sku, qty, unit_price):
        done = self._reserve_all([(sku, qty, unit_price)], wrap=False)
        try:
            self.wallet.charge(qty * unit_price)
        except BaseException:
            for s, q in reversed(done):
                self.store.release(s, q)
            raise
        entry = self.ledger.record("order", sku=sku, qty=qty, total=qty * unit_price, batch=None)
        self._notify([entry])
        return entry

    def place_bulk(self, items):
        lines = [tuple(x) for x in items]
        if not lines:
            return []
        done = self._reserve_all(lines, wrap=True)
        try:
            self.wallet.charge(sum(q * p for _s, q, p in lines))
        except InsufficientFunds as exc:
            for s, q in reversed(done):
                self.store.release(s, q)
            raise BulkError(None, exc) from exc
        batch = self.ledger.next_batch()
        entries = [self.ledger.record("order", sku=s, qty=q, total=q * p, batch=batch) for s, q, p in lines]
        self.ledger.commit_batch()
        self._notify(entries)
        return entries
''',
}
MF1_TEST = r'''from store import Store, OutOfStock, UnknownSku
from wallet import Wallet, InsufficientFunds
from ledger import Ledger
from orders import OrderService, BulkError


def mk(stock=None, money=10_000):
    s, w, l = Store(stock or {"a": 5, "b": 3, "c": 1}), Wallet(money), Ledger()
    return s, w, l, OrderService(s, w, l)


def state(s, w, l):
    return dict(s.stock), w.balance, [dict(e) for e in l.entries]


# plain orders keep working, and are now atomic
s, w, l, svc = mk()
e = svc.place_order("a", 2, 100)
assert e == {"seq": 1, "kind": "order", "sku": "a", "qty": 2, "total": 200, "batch": None}
assert s.stock["a"] == 3 and w.balance == 9800
s, w, l, svc = mk(money=50)
before = state(s, w, l)
try:
    svc.place_order("a", 1, 100)
except InsufficientFunds:
    pass
else:
    raise AssertionError("expected InsufficientFunds")
assert state(s, w, l) == before                      # reservation was released
for bad, exc in [(("zzz", 1, 1), UnknownSku), (("a", 99, 1), OutOfStock), (("a", 0, 1), ValueError)]:
    try:
        svc.place_order(*bad)
    except exc:
        pass
    else:
        raise AssertionError(bad)
assert state(s, w, l) == before

# bulk success: one charge, one ledger entry per line, shared batch number, listeners after commit in order
s, w, l, svc = mk()
seen = []
def spy(entry):
    seen.append((entry["seq"], s.stock["a"], w.balance, len(l.entries)))
svc.listeners.append(spy)
out = svc.place_bulk([("a", 2, 100), ("b", 1, 250), ("a", 1, 100)])
assert [x["seq"] for x in out] == [1, 2, 3] and all(x["batch"] == 1 for x in out)
assert [(x["sku"], x["qty"], x["total"]) for x in out] == [("a", 2, 200), ("b", 1, 250), ("a", 1, 100)]
assert l.entries == out and s.stock == {"a": 2, "b": 2, "c": 1} and w.balance == 10_000 - 550
assert seen == [(1, 2, 9450, 3), (2, 2, 9450, 3), (3, 2, 9450, 3)]
assert svc.place_bulk([]) == [] and len(l.entries) == 3
out2 = svc.place_bulk([("c", 1, 5)])
assert out2[0]["seq"] == 4 and out2[0]["batch"] == 2

# every failure leaves store, wallet and ledger untouched and notifies nobody
def fails(items, index, kind, **kw):
    s, w, l, svc = mk(**kw)
    called = []
    svc.listeners.append(called.append)
    before = state(s, w, l)
    try:
        svc.place_bulk(items)
    except BulkError as e:
        assert e.index == index, (items, e.index)
        assert isinstance(e.cause, kind), (items, e.cause)
    else:
        raise AssertionError(items)
    assert state(s, w, l) == before and called == []
    nxt = svc.place_bulk([("b", 1, 1)])               # sequence numbers and batch numbers were not burned
    assert nxt[0]["seq"] == len(before[2]) + 1 and nxt[0]["batch"] == 1

fails([("a", 1, 10), ("zzz", 1, 10), ("b", 1, 10)], 1, UnknownSku)
fails([("a", 3, 10), ("a", 3, 10)], 1, OutOfStock)                   # same sku twice: quantities add up
fails([("a", 1, 10), ("b", 1, 10), ("c", 2, 10)], 2, OutOfStock)
fails([("a", 1, 10), ("b", 0, 10)], 1, ValueError)
fails([("a", 2, 100), ("b", 2, 100)], None, InsufficientFunds, money=399)

# listener errors never undo a committed order, for single orders and bulks alike
s, w, l, svc = mk()
def boom(entry):
    raise RuntimeError("listener bug")
got = []
svc.listeners += [boom, got.append]
e1 = svc.place_order("a", 1, 10)
out = svc.place_bulk([("a", 1, 10), ("b", 1, 10)])
assert len(l.entries) == 3 and s.stock["a"] == 3 and w.balance == 10_000 - 30
assert got == [e1] + out and len(svc.listener_errors) == 3
assert all(isinstance(x[1], RuntimeError) for x in svc.listener_errors)
print("ok")
'''
task("dev2-mf-bulk-orders", "multi-file",
     "Add atomic bulk ordering: OrderService.place_bulk(items), items being (sku, qty, unit_price) lines.\n"
     "- All lines succeed or nothing changes: on any failure stock, wallet balance and ledger entries (and their seq "
     "numbers) are exactly as before, and no listener is called. Failures raise BulkError (new, in orders.py) with "
     ".index = index of the first line whose reservation failed (unknown sku, out of stock, bad qty; the same sku on "
     "several lines draws on one stock), or None when stock was fine but the wallet could not pay the one combined "
     "charge (sum of qty*unit_price), and .cause = the underlying exception.\n"
     "- On success: one wallet charge for the total and one ledger entry per line (same shape as place_order's, but "
     "with batch=<n>, where n counts successful bulks from 1; failed bulks do not use up a number; place_order's "
     "entries keep batch=None). Returns the list of entries; an empty items list returns [] and changes nothing.\n"
     "- Listeners are called once per entry, in order, only after the whole bulk is committed. A listener that raises "
     "must never undo anything: catch it and append (entry, exception) to service.listener_errors (do this for "
     "place_order too).\n"
     "- place_order currently leaves the stock reserved when the wallet rejects the charge: make it atomic too.",
     MF1_FILES, {"hidden_t.py": MF1_TEST}, "python3 hidden_t.py", MF1_REF, split="dev")

MF2_FILES = {
    "errors.py": "class ConfigError(Exception):\n    pass\n",
    "defaults.py": r'''DEFAULTS = {
    "name": "svc",
    "debug": False,
    "tags": ["base"],
    "ratio": 0.5,
    "db": {"host": "localhost", "port": 5432, "pool": {"size": 5, "timeout": 2.5}},
}
''',
    "coerce.py": r'''from errors import ConfigError

TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}


def coerce(raw, like, key):
    """Convert the string `raw` to the type of the default value `like`."""
    try:
        if isinstance(like, bool):
            low = raw.strip().lower()
            if low in TRUE:
                return True
            if low in FALSE:
                return False
            raise ValueError(raw)
        if isinstance(like, int):
            return int(raw)
        if isinstance(like, float):
            return float(raw)
        if isinstance(like, list):
            return [x.strip() for x in raw.split(",")] if raw.strip() else []
        return raw
    except ValueError:
        raise ConfigError(f"bad value for {key}: {raw!r}") from None
''',
    "keys.py": r'''def leaves(d, prefix=""):
    """Flatten a nested dict to {'a.b': value}; lists and scalars are leaves."""
    out = {}
    for k, v in d.items():
        path = prefix + k
        if isinstance(v, dict):
            out.update(leaves(v, path + "."))
        else:
            out[path] = v
    return out


def set_path(d, path, value):
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value
''',
    "envloader.py": r'''from coerce import coerce
from defaults import DEFAULTS
from errors import ConfigError
from keys import leaves, set_path

PREFIX = "MESH_"


def from_env(env):
    known = leaves(DEFAULTS)
    out = {}
    for name, raw in env.items():
        if not name.startswith(PREFIX):
            continue
        key = name[len(PREFIX):].lower().replace("__", ".")
        if key not in known:
            raise ConfigError(f"unknown setting {key}")
        set_path(out, key, coerce(raw, known[key], key))
    return out
''',
    "fileloader.py": r'''import json

from defaults import DEFAULTS
from errors import ConfigError
from keys import leaves, set_path


def from_file(path):
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ConfigError("config file must hold an object")
    known = leaves(DEFAULTS)
    out = {}
    for key, value in leaves(data).items():
        if key not in known:
            raise ConfigError(f"unknown setting {key}")
        set_path(out, key, value)
    return out
''',
    "settings.py": r'''import copy

from defaults import DEFAULTS
from envloader import from_env
from fileloader import from_file


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


class Settings:
    def __init__(self, data):
        self._data = data

    def get(self, key):
        cur = self._data
        for p in key.split("."):
            cur = cur[p]
        return cur

    def as_dict(self):
        return self._data


def load(path=None, env=None):
    cfg = copy.copy(DEFAULTS)
    _merge(cfg, from_env(env or {}))
    if path:
        _merge(cfg, from_file(path))
    return Settings(cfg)
''',
}
MF2_REF = {
    "keys.py": MF2_FILES["keys.py"],
    "envloader.py": r'''from coerce import coerce
from defaults import DEFAULTS
from errors import ConfigError
from keys import leaves

PREFIX = "MESH_"


def from_env(env):
    """Flat {'db.port': value}."""
    known = leaves(DEFAULTS)
    out = {}
    for name, raw in env.items():
        if not name.startswith(PREFIX):
            continue
        key = name[len(PREFIX):].lower().replace("__", ".")
        if key not in known:
            raise ConfigError(f"unknown setting {key}")
        out[key] = coerce(raw, known[key], key)
    return out
''',
    "fileloader.py": r'''import json

from defaults import DEFAULTS
from errors import ConfigError
from keys import leaves


def _ok(value, like):
    if isinstance(like, bool):
        return isinstance(value, bool)
    if isinstance(like, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(like, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(like, list):
        return isinstance(value, list)
    return isinstance(value, type(like))


def from_file(path):
    """Flat {'db.port': value}."""
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ConfigError("config file must hold an object")
    known = leaves(DEFAULTS)
    out = {}
    for key, value in leaves(data).items():
        if key not in known:
            raise ConfigError(f"unknown setting {key}")
        if not _ok(value, known[key]):
            raise ConfigError(f"bad value for {key}: {value!r}")
        out[key] = float(value) if isinstance(known[key], float) else value
    return out
''',
    "cliloader.py": r'''from coerce import coerce
from defaults import DEFAULTS
from errors import ConfigError
from keys import leaves


def from_argv(argv):
    """Flat {'db.port': value} from repeated --set key=value / --set=key=value; later ones win."""
    known = leaves(DEFAULTS)
    out, i = {}, 0
    argv = list(argv or [])
    while i < len(argv):
        arg = argv[i]
        if arg == "--set":
            i += 1
            if i >= len(argv):
                raise ConfigError("--set needs key=value")
            item = argv[i]
        elif arg.startswith("--set="):
            item = arg[len("--set="):]
        else:
            i += 1
            continue
        i += 1
        if "=" not in item:
            raise ConfigError(f"--set needs key=value, got {item!r}")
        key, raw = item.split("=", 1)
        if key not in known:
            raise ConfigError(f"unknown setting {key}")
        out[key] = coerce(raw, known[key], key)
    return out
''',
    "settings.py": r'''import copy

from defaults import DEFAULTS
from cliloader import from_argv
from envloader import from_env
from fileloader import from_file
from keys import leaves, set_path


class Settings:
    def __init__(self, flat, sources):
        self._flat = flat
        self._sources = sources

    def get(self, key):
        if key in self._flat:
            v = self._flat[key]
            return copy.deepcopy(v)
        sub = {k[len(key) + 1:]: v for k, v in self._flat.items() if k.startswith(key + ".")}
        if not sub:
            raise KeyError(key)
        out = {}
        for k, v in sub.items():
            set_path(out, k, copy.deepcopy(v))
        return out

    def as_dict(self):
        out = {}
        for k, v in self._flat.items():
            set_path(out, k, copy.deepcopy(v))
        return out

    def source(self, key):
        return self._sources[key]


def load(path=None, env=None, argv=None):
    flat = copy.deepcopy(leaves(DEFAULTS))
    sources = {k: "default" for k in flat}
    layers = [("file", from_file(path) if path else {}), ("env", from_env(env or {})), ("cli", from_argv(argv))]
    for name, layer in layers:
        for k, v in layer.items():
            flat[k] = v
            sources[k] = name
    return Settings(flat, sources)
''',
}
MF2_TEST = r'''import json, os, tempfile
import defaults
from errors import ConfigError
import settings

ORIG = json.loads(json.dumps(defaults.DEFAULTS))


def write(obj):
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    return p


def bad(**kw):
    try:
        settings.load(**kw)
    except ConfigError as e:
        return str(e)
    raise AssertionError(kw)


s = settings.load()
assert s.get("db.port") == 5432 and s.get("db.pool.size") == 5 and s.get("name") == "svc"
assert s.source("db.port") == "default" and s.as_dict() == ORIG
assert s.get("db") == ORIG["db"] and s.get("db.pool") == {"size": 5, "timeout": 2.5}

path = write({"db": {"port": 6000, "pool": {"size": 7}}, "tags": ["x", "y"], "ratio": 1, "name": "filed"})
env = {"MESH_DB__PORT": "6100", "MESH_DB__POOL__TIMEOUT": "9.5", "MESH_DEBUG": "Yes", "MESH_NAME": "env", "OTHER": "z",
       "PATH": "/bin"}
argv = ["prog", "--set", "db.port=7000", "--verbose", "--set=name=a=b", "--set", "tags=p, q ,r", "--set", "debug=off"]
s = settings.load(path, env, argv)
# precedence: defaults < file < env < --set
assert s.get("db.port") == 7000 and s.source("db.port") == "cli"
assert s.get("db.pool.size") == 7 and s.source("db.pool.size") == "file"
assert s.get("db.pool.timeout") == 9.5 and s.source("db.pool.timeout") == "env"
assert s.get("name") == "a=b" and s.source("name") == "cli"
assert s.get("tags") == ["p", "q", "r"] and s.get("debug") is False and s.source("debug") == "cli"
assert s.get("ratio") == 1.0 and isinstance(s.get("ratio"), float) and s.source("ratio") == "file"
assert s.get("db.host") == "localhost" and s.source("db.host") == "default"
s2 = settings.load(path, {"MESH_DEBUG": "1", "MESH_NAME": "env"}, None)
assert s2.get("debug") is True and s2.get("name") == "env" and s2.source("name") == "env" and s2.get("tags") == ["x", "y"]
assert settings.load(None, {"MESH_TAGS": ""}, None).get("tags") == []
assert settings.load(argv=["--set", "db.port=1", "--set", "db.port=2"]).get("db.port") == 2

# values are copies; DEFAULTS is never touched; loads are independent
d = s.as_dict()
d["db"]["pool"]["size"] = 99
d["tags"].append("zzz")
s.get("tags").append("nope")
assert s.get("db.pool.size") == 7 and s.get("tags") == ["p", "q", "r"]
a, b = settings.load(), settings.load()
a.as_dict()["tags"].append("leak"); a.get("db.pool")["size"] = 0
assert b.get("tags") == ["base"] and b.get("db.pool.size") == 5 and defaults.DEFAULTS == ORIG
settings.load(path, env, argv)
assert defaults.DEFAULTS == ORIG

# validation
assert "nope" in bad(argv=["--set", "nope=1"]) and "db.nope" in bad(env={"MESH_DB__NOPE": "1"})
assert "zzz" in bad(path=write({"db": {"zzz": 1}}))
assert "db.port" in bad(argv=["--set", "db.port=abc"]) and "db.port" in bad(argv=["--set", "db.port=5.0"])
assert "debug" in bad(argv=["--set", "debug=maybe"]) and "db.port" in bad(env={"MESH_DB__PORT": "x"})
bad(argv=["--set", "db.pool=3"]); bad(argv=["--set"]); bad(argv=["--set", "novalue"]); bad(argv=["--set=also"])
assert "db.port" in bad(path=write({"db": {"port": "6000"}})) and "debug" in bad(path=write({"debug": 1}))
assert "db.port" in bad(path=write({"db": {"port": True}})) and "tags" in bad(path=write({"tags": "a,b"}))
bad(path=write([1, 2]))
print("ok")
'''
task("dev2-mf-config-layers", "multi-file",
     "Settings come from defaults, a JSON file, MESH_* environment variables and now the command line. Extend "
     "settings.load(path=None, env=None, argv=None) so argv may contain repeated `--set key=value` (also `--set=key=value`; "
     "other args are ignored; later --set for the same key wins; the value is split at the first '=') and the "
     "effective value of every key follows the precedence defaults < file < env < --set. (Today env is applied "
     "before the file, so the file wins over env; that is wrong.) Settings gets source(key) -> 'default' | 'file' | "
     "'env' | 'cli' for any leaf key such as 'db.pool.size', and get() of a dotted leaf or of a sub-tree like "
     "'db.pool' keeps working.\nRules for every layer: unknown keys raise ConfigError naming the key; --set values are "
     "converted with the same rules as env values (type of the default, via coerce); setting a non-leaf like "
     "`--set db.pool=3`, a missing '=', or a dangling --set raise ConfigError; file values are not converted but must "
     "have the right JSON type (int is fine where a float is expected and is returned as float; bool is NOT an int; "
     "a string is not a list), otherwise ConfigError naming the key. DEFAULTS must never be modified and objects "
     "returned by get()/as_dict() must be copies that cannot change the Settings or later loads.",
     MF2_FILES, {"hidden_t.py": MF2_TEST}, "python3 hidden_t.py", MF2_REF, split="dev")

# =====================================================================================================================
# PERFORMANCE
# =====================================================================================================================
NEAR_SRC = r'''def nearest(points, queries):
    """For each query return the index in `points` of the closest point.
    Equal distances: the lowest index wins (so among equal point values, the first one). No points: -1."""
    out = []
    for q in queries:
        best = None
        for i, p in enumerate(points):
            d = abs(p - q)
            if best is None or d < best[0]:
                best = (d, i)
        out.append(best[1] if best else -1)
    return out
'''
NEAR_REF = r'''from bisect import bisect_left


def nearest(points, queries):
    """For each query return the index in `points` of the closest point.
    Equal distances: the lowest index wins (so among equal point values, the first one). No points: -1."""
    if not points:
        return [-1] * len(queries)
    first = {}
    for i, p in enumerate(points):
        first.setdefault(p, i)
    vals = sorted(first)
    out = []
    for q in queries:
        j = bisect_left(vals, q)
        cands = []
        if j < len(vals):
            cands.append(vals[j])
        if j > 0:
            cands.append(vals[j - 1])
        best = min(cands, key=lambda v: (abs(v - q), first[v]))
        out.append(first[best])
    return out
'''
NEAR_TEST = r'''import random, time
from nearest import nearest


def slow(points, queries):
    out = []
    for q in queries:
        best = None
        for i, p in enumerate(points):
            d = abs(p - q)
            if best is None or d < best[0]:
                best = (d, i)
        out.append(best[1] if best else -1)
    return out


assert nearest([], [1, 2]) == [-1, -1] and nearest([5], []) == [] and nearest([5, 5], [100, -100]) == [0, 0]
assert nearest([10, 20, 10], [15]) == [0]            # tie 10 vs 20: lowest index (0)
assert nearest([20, 10, 10], [15]) == [0]            # tie: index 0 holds 20
assert nearest([1, 3], [2]) == [0] and nearest([3, 1], [2]) == [0]
rnd = random.Random(5)
for _ in range(400):
    n, m = rnd.randint(0, 25), rnd.randint(0, 25)
    kind = rnd.random()
    if kind < 0.4:
        pts = [rnd.randint(-8, 8) for _ in range(n)]; qs = [rnd.randint(-10, 10) for _ in range(m)]
    elif kind < 0.7:
        pts = [rnd.randint(-8, 8) / 2 for _ in range(n)]; qs = [rnd.randint(-20, 20) / 4 for _ in range(m)]
    else:
        pts = [rnd.choice([0.1, 0.2, 0.3, 0.7]) for _ in range(n)]; qs = [rnd.choice([0.15, 0.25, 0.5, 0.0, 1.0]) for _ in range(m)]
    assert nearest(pts, qs) == slow(pts, qs), (pts, qs)
rnd = random.Random(6)
pts = [rnd.randint(-10 ** 6, 10 ** 6) for _ in range(60000)]
qs = [rnd.randint(-10 ** 6, 10 ** 6) + rnd.choice([0, 0.5]) for _ in range(60000)]
t = time.perf_counter(); got = nearest(pts, qs); dt = time.perf_counter() - t
assert dt < 4, dt
assert got[:40] == slow(pts, qs[:40]) and got[-40:] == slow(pts, qs[-40:])
print("ok")
'''
task("dev2-perf-nearest", "performance",
     "nearest.nearest() is correct but far too slow for 60k points and 60k queries (the nightly job times out). "
     "Make it fast while returning exactly the same indices for every input, including ties and duplicate values, "
     "ints and floats mixed, and an empty point list.",
     {"nearest.py": NEAR_SRC}, {"hidden_t.py": NEAR_TEST}, "python3 hidden_t.py", {"nearest.py": NEAR_REF},
     split="dev", timeout=300)

WIN_SRC = r'''def distinct_in_window(events, width):
    """events: list of (ts, user), any order, ts ints. For every event (in input order) return how many distinct
    users appear among all events j with ts_i - width <= ts_j <= ts_i (the event itself and every event with the same
    timestamp, wherever it is in the list, are included). A negative width gives 0 for every event."""
    out = []
    for ts, _user in events:
        seen = set()
        for t, u in events:
            if ts - width <= t <= ts:
                seen.add(u)
        out.append(len(seen))
    return out
'''
WIN_REF = r'''def distinct_in_window(events, width):
    """events: list of (ts, user), any order, ts ints. For every event (in input order) return how many distinct
    users appear among all events j with ts_i - width <= ts_j <= ts_i (the event itself and every event with the same
    timestamp, wherever it is in the list, are included). A negative width gives 0 for every event."""
    n = len(events)
    if width < 0:
        return [0] * n
    order = sorted(range(n), key=lambda i: events[i][0])
    out = [0] * n
    counts, distinct = {}, 0
    left = right = 0
    while right < n:
        ts = events[order[right]][0]
        group_end = right
        while group_end < n and events[order[group_end]][0] == ts:
            u = events[order[group_end]][1]
            c = counts.get(u, 0)
            if c == 0:
                distinct += 1
            counts[u] = c + 1
            group_end += 1
        while events[order[left]][0] < ts - width:
            u = events[order[left]][1]
            counts[u] -= 1
            if counts[u] == 0:
                distinct -= 1
            left += 1
        for k in range(right, group_end):
            out[order[k]] = distinct
        right = group_end
    return out
'''
WIN_TEST = r'''import random, time
from window import distinct_in_window


def slow(events, width):
    return [len({u for t, u in events if ts - width <= t <= ts}) for ts, _ in events]


assert distinct_in_window([], 5) == []
assert distinct_in_window([(5, "a"), (5, "b"), (5, "a")], 0) == [2, 2, 2]
assert distinct_in_window([(5, "a"), (3, "b"), (4, "a")], 1) == [1, 1, 2] and distinct_in_window([(5, "a"), (3, "b")], -1) == [0, 0]
assert distinct_in_window([(10, "a"), (1, "b"), (10, "b"), (9, "c")], 1) == [3, 1, 3, 1]
rnd = random.Random(2)
for _ in range(500):
    n = rnd.randint(0, 30)
    ev = [(rnd.randint(-10, 20), rnd.choice("abcde")) for _ in range(n)]
    w = rnd.randint(-2, 12)
    assert distinct_in_window(ev, w) == slow(ev, w), (ev, w)
rnd = random.Random(3)
ev = [(rnd.randint(0, 2_000_000), f"u{rnd.randint(0, 3000)}") for _ in range(70000)]
t = time.perf_counter(); got = distinct_in_window(ev, 20000); dt = time.perf_counter() - t
assert dt < 4, dt
sub = ev[:300]
assert got[:0] == [] and distinct_in_window(sub, 20000) == slow(sub, 20000)
for i in (0, 1, 777, 35000, 69999):
    ts = ev[i][0]
    assert got[i] == len({u for t2, u in ev if ts - 20000 <= t2 <= ts}), i
print("ok")
'''
task("dev2-perf-window-distinct", "performance",
     "window.distinct_in_window() produces the right numbers but the analytics job with 70k events takes minutes. "
     "Make it fast without changing any result (events can arrive unsorted, timestamps repeat, width can be 0 or "
     "negative; results stay in input order).",
     {"window.py": WIN_SRC}, {"hidden_t.py": WIN_TEST}, "python3 hidden_t.py", {"window.py": WIN_REF},
     split="dev", timeout=300)

# =====================================================================================================================
# AMBIGUOUS
# =====================================================================================================================
MONEY_SRC = r'''from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _quantize(amount):
    try:
        return Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"not a number: {amount!r}") from None


def format_usd(amount):
    if amount is None:
        return ""
    d = _quantize(amount)
    s = f"${abs(d):,.2f}"
    return "-" + s if d < 0 else s


def format_eur(amount):
    if amount is None:
        return ""
    d = _quantize(amount)
    s = f"{abs(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
    return "-" + s if d < 0 else s


FORMATTERS = {"USD": format_usd, "EUR": format_eur}


def supported():
    return sorted(FORMATTERS)


def format_money(amount, currency):
    try:
        fn = FORMATTERS[currency.upper()]
    except KeyError:
        raise ValueError(f"unsupported currency: {currency}") from None
    return fn(amount)
'''
MONEY_TEST = r'''from decimal import Decimal
from money import format_money, format_usd, format_eur, supported


def test_usd():
    assert format_usd(1234.5) == "$1,234.50"
    assert format_usd(-1234.5) == "-$1,234.50"
    assert format_usd(0) == "$0.00"
    assert format_usd(None) == ""
    assert format_usd("2.675") == "$2.68" and format_usd("-2.675") == "-$2.68"
    assert format_usd(-0.004) == "$0.00"
    assert format_usd(Decimal("1000000")) == "$1,000,000.00"
    assert format_usd(0.1 + 0.2) == "$0.30"


def test_eur():
    assert format_eur(1234.5) == "1.234,50 €"
    assert format_eur(-1234.5) == "-1.234,50 €"
    assert format_eur(None) == ""


def test_dispatch():
    assert format_money(5, "usd") == "$5.00"
    assert format_money(5, "EUR") == "5,00 €"
    try:
        format_money(5, "XXX")
    except ValueError as e:
        assert "XXX" in str(e)
    else:
        raise AssertionError
    try:
        format_money("abc", "USD")
    except ValueError:
        pass
    else:
        raise AssertionError
    assert supported() == ["EUR", "USD"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
'''
MONEY_REF = MONEY_SRC.replace('FORMATTERS = {"USD": format_usd, "EUR": format_eur}', r'''def format_gbp(amount):
    if amount is None:
        return ""
    d = _quantize(amount)
    s = f"£{abs(d):,.2f}"
    return "-" + s if d < 0 else s


FORMATTERS = {"USD": format_usd, "EUR": format_eur, "GBP": format_gbp}''')
MONEY_HIDDEN = r'''from decimal import Decimal
import money
from money import format_money, supported
import test_money
test_money.test_usd(); test_money.test_eur()
assert supported() == ["EUR", "GBP", "USD"]
f = money.FORMATTERS["GBP"]
assert f(1234.5) == "£1,234.50" and f(-1234.5) == "-£1,234.50" and f(0) == "£0.00" and f(None) == ""
assert f("2.675") == "£2.68" and f("-2.675") == "-£2.68" and f(-0.004) == "£0.00"
assert f(Decimal("1000000")) == "£1,000,000.00" and f(0.1 + 0.2) == "£0.30" and f(7) == "£7.00"
assert format_money(5, "gbp") == "£5.00" and format_money(-1234567.891, "GBP") == "-£1,234,567.89"
for bad in ("abc", "1,5", object()):
    try:
        format_money(bad, "GBP")
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
try:
    format_money(1, "JPY")
except ValueError as e:
    assert "JPY" in str(e)
else:
    raise AssertionError
print("ok")
'''
task("dev2-amb-gbp", "ambiguous",
     "Add GBP to the money formatter.",
     {"money.py": MONEY_SRC, "test_money.py": MONEY_TEST,
      "README.md": "# money\n\nFormatting helpers. One `format_<ccy>` function per currency, registered in "
                   "`FORMATTERS`; `format_money(amount, code)` dispatches. Amounts are rounded half up to 2 places, "
                   "negatives get a leading minus, None formats as an empty string.\n"},
     {"hidden_t.py": MONEY_HIDDEN}, "python3 hidden_t.py", {"money.py": MONEY_REF}, split="dev")

TODO_SRC = r'''import datetime
import json
import sys


class UsageError(Exception):
    pass


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return []


def _save(path, tasks):
    with open(path, "w") as fh:
        json.dump(tasks, fh, indent=1)


def _opts(args, allowed):
    opts, rest, i = {}, [], 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            name, eq, val = a[2:].partition("=")
            if name not in allowed:
                raise UsageError(f"unknown option --{name}")
            if not eq:
                i += 1
                if i >= len(args):
                    raise UsageError(f"--{name} needs a value")
                val = args[i]
            opts[name] = val
        else:
            rest.append(a)
        i += 1
    return opts, rest


def _due(s):
    if s == "":
        return None
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except ValueError:
        raise UsageError(f"invalid due date {s!r} (expected YYYY-MM-DD)") from None


def _line(t):
    s = f"{t['id']}. [{'x' if t['done'] else ' '}] {t['title']}"
    if t.get("due"):
        s += f" (due {t['due']})"
    if t.get("priority", 2) != 2:
        s += f" !{t['priority']}"
    return s


def _find(tasks, raw):
    try:
        tid = int(raw)
    except ValueError:
        raise UsageError(f"invalid id {raw!r}") from None
    for t in tasks:
        if t["id"] == tid:
            return t
    raise UsageError(f"no task {tid}")


def main(argv, path="tasks.json", out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    try:
        if not argv:
            raise UsageError("usage: todo add|list|done|edit ...")
        cmd, args = argv[0], argv[1:]
        tasks = _load(path)
        if cmd == "add":
            opts, rest = _opts(args, {"due"})
            if len(rest) != 1:
                raise UsageError("add needs exactly one title")
            tid = max([t["id"] for t in tasks], default=0) + 1
            tasks.append({"id": tid, "title": rest[0], "done": False,
                          "due": _due(opts.get("due", "")), "priority": 2})
            _save(path, tasks)
            print(f"added {tid}", file=out)
        elif cmd == "list":
            show_all = "--all" in args
            rows = [t for t in tasks if show_all or not t["done"]]
            rows.sort(key=lambda t: (t.get("priority", 2), t.get("due") or "9999-12-31", t["id"]))
            for t in rows:
                print(_line(t), file=out)
        elif cmd == "done":
            if len(args) != 1:
                raise UsageError("done needs an id")
            _find(tasks, args[0])["done"] = True
            _save(path, tasks)
        elif cmd == "edit":
            opts, rest = _opts(args, {"title", "due"})
            if len(rest) != 1:
                raise UsageError("edit needs an id")
            t = _find(tasks, rest[0])
            if "title" in opts:
                t["title"] = opts["title"]
            if "due" in opts:
                t["due"] = _due(opts["due"])
            _save(path, tasks)
        else:
            raise UsageError(f"unknown command {cmd!r}")
        return 0
    except UsageError as e:
        print(f"error: {e}", file=err)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''
TODO_VISIBLE = r'''import io, os, tempfile
import todo


def run(path, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = todo.main(list(argv), path, out, err)
    return code, out.getvalue(), err.getvalue()


def test_add_list_done():
    p = os.path.join(tempfile.mkdtemp(), "t.json")
    assert run(p, "add", "buy milk", "--due", "2026-01-05")[:2] == (0, "added 1\n")
    run(p, "add", "write report")
    assert run(p, "list")[1] == "1. [ ] buy milk (due 2026-01-05)\n2. [ ] write report\n"
    run(p, "done", "1")
    assert run(p, "list")[1] == "2. [ ] write report\n"
    assert run(p, "list", "--all")[1] == "1. [x] buy milk (due 2026-01-05)\n2. [ ] write report\n"


def test_errors():
    p = os.path.join(tempfile.mkdtemp(), "t.json")
    code, _, err = run(p, "add", "x", "--due", "soon")
    assert code == 2 and err.startswith("error: invalid due date 'soon'")
    assert run(p, "add", "x", "--colour", "red")[0] == 2
    assert run(p, "add", "x", "--due")[0] == 2
    assert run(p, "done", "9")[0] == 2


def test_edit():
    p = os.path.join(tempfile.mkdtemp(), "t.json")
    run(p, "add", "a", "--due", "2026-02-01")
    assert run(p, "edit", "1", "--title", "b", "--due", "")[0] == 0
    assert run(p, "list")[1] == "1. [ ] b\n"


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
'''
TODO_REF = TODO_SRC.replace('''def _line(t):''', '''def _priority(s):
    try:
        p = int(s)
    except ValueError:
        p = 0
    if not 1 <= p <= 3:
        raise UsageError(f"invalid priority {s!r} (expected 1-3)")
    return p


def _line(t):''').replace('''_opts(args, {"due"})''', '''_opts(args, {"due", "priority"})''').replace(
    '''"due": _due(opts.get("due", "")), "priority": 2})''',
    '''"due": _due(opts.get("due", "")),
                          "priority": _priority(opts["priority"]) if "priority" in opts else 2})''').replace(
    '''_opts(args, {"title", "due"})''', '''_opts(args, {"title", "due", "priority"})''').replace(
    '''            _save(path, tasks)
        else:''', '''            if "priority" in opts:
                t["priority"] = _priority(opts["priority"])
            _save(path, tasks)
        else:''')
TODO_HIDDEN = r'''import io, json, os, tempfile
import todo
import test_todo
test_todo.test_add_list_done(); test_todo.test_errors(); test_todo.test_edit()


def run(path, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = todo.main(list(argv), path, out, err)
    return code, out.getvalue(), err.getvalue()


p = os.path.join(tempfile.mkdtemp(), "t.json")
assert run(p, "add", "low", "--priority", "3")[:2] == (0, "added 1\n")
assert run(p, "add", "normal", "--due", "2026-01-02")[0] == 0
assert run(p, "add", "urgent", "--priority=1", "--due", "2026-05-05")[0] == 0
assert [t["priority"] for t in json.load(open(p))] == [3, 2, 1]
assert run(p, "list")[1] == "3. [ ] urgent (due 2026-05-05) !1\n2. [ ] normal (due 2026-01-02)\n1. [ ] low !3\n"
assert run(p, "edit", "1", "--priority", "1")[0] == 0 and json.load(open(p))[0]["priority"] == 1
assert run(p, "list")[1].splitlines()[0] == "3. [ ] urgent (due 2026-05-05) !1" and run(p, "list")[1].splitlines()[1] == "1. [ ] low !1"
assert run(p, "edit", "2", "--priority", "2", "--title", "norm")[0] == 0 and json.load(open(p))[1]["title"] == "norm"
before = open(p).read()
for bad in ("0", "4", "high", "1.5", "-1", "", "x1"):
    for args in (("add", "z", "--priority", bad), ("edit", "2", "--priority", bad)):
        code, out, err = run(p, *args)
        assert code == 2 and out == "" and err.startswith("error:") and "priority" in err, (args, code, err)
        assert bad == "" or repr(bad) in err or bad in err, (args, err)
    assert open(p).read() == before                    # nothing was saved
assert run(p, "add", "z", "--priority")[0] == 2 and run(p, "add", "z", "-p", "1")[0] == 2
assert open(p).read() == before
# legacy files without a priority field still list and sort as priority 2
q = os.path.join(tempfile.mkdtemp(), "old.json")
json.dump([{"id": 1, "title": "old", "done": False, "due": None}, {"id": 2, "title": "hi", "done": False, "due": None, "priority": 1}], open(q, "w"))
assert run(q, "list")[1] == "2. [ ] hi !1\n1. [ ] old\n"
assert run(q, "edit", "1", "--priority", "3")[0] == 0 and run(q, "list")[1].endswith("1. [ ] old !3\n")
print("ok")
'''
task("dev2-amb-priority", "ambiguous",
     "Let me set a priority on tasks.",
     {"todo.py": TODO_SRC, "test_todo.py": TODO_VISIBLE,
      "README.md": "# todo\n\n`todo add TITLE [--due YYYY-MM-DD]`, `todo list [--all]`, `todo done ID`, "
                   "`todo edit ID [--title T] [--due D]`.\n\nConventions: long options only, `--opt value` or "
                   "`--opt=value`; every user error prints `error: <message>` to stderr and exits 2 without touching "
                   "the data file. Tasks list by priority (1 is most urgent, 3 least, 2 default), then due date.\n"},
     {"hidden_t.py": TODO_HIDDEN}, "python3 hidden_t.py", {"todo.py": TODO_REF}, split="dev")

# =====================================================================================================================
# REFACTORING
# =====================================================================================================================
PRICING_LEGACY = r'''def price_order(order):
    lines = order.get("lines") or []
    if not lines:
        raise ValueError("empty order")
    gross = 0
    line_disc = 0
    for ln in lines:
        q = ln["qty"]
        if not isinstance(q, int) or isinstance(q, bool) or q <= 0:
            raise ValueError("bad qty for " + str(ln["sku"]))
        if ln["unit"] < 0:
            raise ValueError("bad price for " + str(ln["sku"]))
        g = q * ln["unit"]
        gross += g
        if q >= 10:
            line_disc += (g * 10 + 50) // 100
    country = order.get("country", "US")
    rates = {"US": 0, "DE": 19, "FR": 20}
    if country not in rates:
        raise ValueError("unknown country " + str(country))
    after = gross - line_disc
    tier = (order.get("customer") or {}).get("tier")
    tier_disc = 0
    if tier == "gold":
        tier_disc = (after * 5 + 50) // 100
    elif tier == "silver":
        tier_disc = (after * 2 + 50) // 100
    after2 = after - tier_disc
    coupon = order.get("coupon")
    coupon = coupon.strip().upper() if coupon else None
    coupon_disc = 0
    free_ship = False
    if coupon == "SAVE10":
        coupon_disc = min((after2 * 10 + 50) // 100, 2000)
    elif coupon == "FIVEOFF":
        coupon_disc = min(500, after2)
    elif coupon == "FREESHIP":
        free_ship = True
    net = after2 - coupon_disc
    if free_ship:
        ship = 0
    else:
        ship = 0 if net >= 10000 else 799
        if country != "US":
            ship += 1500
    tax = (net * rates[country] + 50) // 100
    return {"gross": gross, "line_discount": line_disc, "tier_discount": tier_disc, "coupon_discount": coupon_disc,
            "net": net, "shipping": ship, "tax": tax, "total": net + ship + tax}
'''
PRICING_REF = r'''RATES = {"US": 0, "DE": 19, "FR": 20}


def _pct(amount, pct):
    return (amount * pct + 50) // 100


def validate(order):
    lines = order.get("lines") or []
    if not lines:
        raise ValueError("empty order")
    for ln in lines:
        q = ln["qty"]
        if not isinstance(q, int) or isinstance(q, bool) or q <= 0:
            raise ValueError("bad qty for " + str(ln["sku"]))
        if ln["unit"] < 0:
            raise ValueError("bad price for " + str(ln["sku"]))
    country = order.get("country", "US")
    if country not in RATES:
        raise ValueError("unknown country " + str(country))


def line_totals(order):
    out = []
    for ln in order["lines"]:
        g = ln["qty"] * ln["unit"]
        out.append((g, _pct(g, 10) if ln["qty"] >= 10 else 0))
    return out


def tier_discount(amount, tier):
    return _pct(amount, {"gold": 5, "silver": 2}.get(tier, 0))


def _norm_coupon(coupon):
    return coupon.strip().upper() if coupon else None


def coupon_discount(amount, coupon):
    coupon = _norm_coupon(coupon)
    if coupon == "SAVE10":
        return min(_pct(amount, 10), 2000)
    if coupon == "FIVEOFF":
        return min(500, amount)
    return 0


def shipping(amount, country, coupon):
    if _norm_coupon(coupon) == "FREESHIP":
        return 0
    return (0 if amount >= 10000 else 799) + (1500 if country != "US" else 0)


def tax(amount, country):
    return _pct(amount, RATES[country])


def price_order(order):
    validate(order)
    country = order.get("country", "US")
    totals = line_totals(order)
    gross = sum(g for g, _ in totals)
    line_disc = sum(d for _, d in totals)
    after = gross - line_disc
    tier_disc = tier_discount(after, (order.get("customer") or {}).get("tier"))
    coupon = order.get("coupon")
    coupon_disc = coupon_discount(after - tier_disc, coupon)
    net = after - tier_disc - coupon_disc
    ship, tx = shipping(net, country, coupon), tax(net, country)
    return {"gross": gross, "line_discount": line_disc, "tier_discount": tier_disc, "coupon_discount": coupon_disc,
            "net": net, "shipping": ship, "tax": tx, "total": net + ship + tx}
'''
PRICING_TEST = r'''import ast, copy, random
import pricing

ns = {}
exec(LEGACY := r"""''' + PRICING_LEGACY.replace('"""', "'''") + r'''""", ns)
legacy = ns["price_order"]


def outcome(fn, order):
    o = copy.deepcopy(order)
    try:
        r = fn(o)
    except Exception as e:
        return ("err", type(e).__name__, str(e))
    assert o == order, "order was mutated"
    return ("ok", r)


rnd = random.Random(12)
skus = ["A", "B", "C", "D"]
for i in range(6000):
    order = {"lines": [{"sku": rnd.choice(skus), "qty": rnd.choice([1, 2, 5, 9, 10, 11, 25, 0, -1, True, 3]),
                        "unit": rnd.choice([0, 1, 99, 250, 1999, 5005, -5])} for _ in range(rnd.choice([0, 1, 1, 2, 3, 6]))]}
    if rnd.random() < 0.9:
        order["country"] = rnd.choice(["US", "DE", "FR", "US", "JP"])
    if rnd.random() < 0.7:
        order["customer"] = rnd.choice([{"tier": "gold"}, {"tier": "silver"}, {"tier": "bronze"}, {}, None])
    if rnd.random() < 0.7:
        order["coupon"] = rnd.choice(["SAVE10", " save10 ", "FIVEOFF", "freeship", "NOPE", "", None])
    if rnd.random() < 0.05:
        order["lines"] = None
    assert outcome(pricing.price_order, order) == outcome(legacy, order), order

# new structure: pure helpers with these exact behaviours
validate = pricing.validate
for bad in ({}, {"lines": []}, {"lines": [{"sku": "A", "qty": 0, "unit": 1}]}, {"lines": [{"sku": "A", "qty": 1, "unit": 1}], "country": "XX"}):
    try:
        validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
assert validate({"lines": [{"sku": "A", "qty": 1, "unit": 1}]}) is None
assert pricing.line_totals({"lines": [{"sku": "A", "qty": 10, "unit": 105}, {"sku": "B", "qty": 2, "unit": 7}]}) == [(1050, 105), (14, 0)]
assert pricing.line_totals({"lines": [{"sku": "A", "qty": 10, "unit": 5}]}) == [(50, 5)]
assert pricing.tier_discount(1050, "gold") == 53 and pricing.tier_discount(1050, "silver") == 21 and pricing.tier_discount(1050, None) == 0
assert pricing.tier_discount(10, "gold") == 1 and pricing.tier_discount(9, "gold") == 0
assert pricing.coupon_discount(50000, "SAVE10") == 2000 and pricing.coupon_discount(1005, " save10") == 101
assert pricing.coupon_discount(300, "FIVEOFF") == 300 and pricing.coupon_discount(900, "fiveoff") == 500
assert pricing.coupon_discount(900, "FREESHIP") == 0 and pricing.coupon_discount(900, None) == 0
assert pricing.shipping(9999, "US", None) == 799 and pricing.shipping(10000, "US", "") == 0
assert pricing.shipping(9999, "DE", "x") == 2299 and pricing.shipping(10000, "FR", None) == 1500
assert pricing.shipping(1, "DE", " freeship ") == 0
assert pricing.tax(1005, "DE") == 191 and pricing.tax(1005, "US") == 0 and pricing.tax(50, "FR") == 10

tree = ast.parse(open("pricing.py").read())
fn = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
po = fn["price_order"]
assert po.end_lineno - po.lineno + 1 <= 24, "price_order should only orchestrate"
calls = {c.func.id for c in ast.walk(po) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
assert {"validate", "line_totals", "tier_discount", "coupon_discount", "shipping", "tax"} <= calls, calls
for name in ("validate", "line_totals", "tier_discount", "coupon_discount", "shipping", "tax"):
    n = fn[name]
    assert n.end_lineno - n.lineno + 1 <= 24, name
    assert not any(isinstance(x, (ast.Global, ast.Nonlocal)) for x in ast.walk(n))
print("ok")
'''
task("dev2-ref-pricing", "refactoring",
     "pricing.price_order() has grown into one 50-line function that nobody dares to touch. Refactor pricing.py "
     "without changing any behaviour (results, error types and messages, and not mutating the order) into small "
     "module-level functions that price_order() merely composes (at most ~24 lines itself, each helper also short):\n"
     "- validate(order) -> None, raises the same ValueErrors as today;\n"
     "- line_totals(order) -> list of (gross, line_discount) per line;\n"
     "- tier_discount(amount, tier) -> int;\n"
     "- coupon_discount(amount, coupon) -> int (coupon text is stripped and case-insensitive; FREESHIP gives no money off);\n"
     "- shipping(amount, country, coupon) -> int (amount is the net after all discounts);\n"
     "- tax(amount, country) -> int.\n"
     "Rounding rules stay exactly as they are.",
     {"pricing.py": PRICING_LEGACY}, {"hidden_t.py": PRICING_TEST}, "python3 hidden_t.py",
     {"pricing.py": PRICING_REF}, split="dev")

LOADERS_LEGACY = r'''import csv


def parse_users(text):
    if text.startswith("﻿"):
        text = text[1:]
    rows = csv.reader(text.splitlines())
    out = []
    header_seen = False
    rd = rows
    for row in rd:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = rd.line_num
        row = [c.strip() for c in row]
        if not header_seen:
            if row != ["id", "name", "email", "age", "active"]:
                raise ValueError("users: bad header " + repr(row))
            header_seen = True
            continue
        if len(row) != 5:
            raise ValueError("users line " + str(n) + ": expected 5 columns, got " + str(len(row)))
        rec = {}
        if row[0] == "":
            raise ValueError("users line " + str(n) + ": missing value for id")
        try:
            rec["id"] = int(row[0])
        except ValueError:
            raise ValueError("users line " + str(n) + ": bad int for id: " + repr(row[0])) from None
        if row[1] == "":
            raise ValueError("users line " + str(n) + ": missing value for name")
        rec["name"] = row[1]
        if row[2] == "":
            raise ValueError("users line " + str(n) + ": missing value for email")
        if "@" not in row[2]:
            raise ValueError("users line " + str(n) + ": bad email for email: " + repr(row[2]))
        rec["email"] = row[2].lower()
        if row[3] == "":
            rec["age"] = None
        else:
            try:
                rec["age"] = int(row[3])
            except ValueError:
                raise ValueError("users line " + str(n) + ": bad int for age: " + repr(row[3])) from None
        if row[4] == "":
            rec["active"] = True
        else:
            v = row[4].lower()
            if v in ("yes", "true", "1"):
                rec["active"] = True
            elif v in ("no", "false", "0"):
                rec["active"] = False
            else:
                raise ValueError("users line " + str(n) + ": bad bool for active: " + repr(row[4]))
        out.append(rec)
    if not header_seen:
        raise ValueError("users: empty file")
    return out


def parse_orders(text):
    if text.startswith("﻿"):
        text = text[1:]
    rd = csv.reader(text.splitlines())
    out = []
    header_seen = False
    for row in rd:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = rd.line_num
        row = [c.strip() for c in row]
        if not header_seen:
            if row != ["order_id", "user_id", "total", "note"]:
                raise ValueError("orders: bad header " + repr(row))
            header_seen = True
            continue
        if len(row) != 4:
            raise ValueError("orders line " + str(n) + ": expected 4 columns, got " + str(len(row)))
        rec = {}
        if row[0] == "":
            raise ValueError("orders line " + str(n) + ": missing value for order_id")
        rec["order_id"] = row[0]
        if row[1] == "":
            raise ValueError("orders line " + str(n) + ": missing value for user_id")
        try:
            rec["user_id"] = int(row[1])
        except ValueError:
            raise ValueError("orders line " + str(n) + ": bad int for user_id: " + repr(row[1])) from None
        if row[2] == "":
            raise ValueError("orders line " + str(n) + ": missing value for total")
        try:
            rec["total"] = float(row[2])
        except ValueError:
            raise ValueError("orders line " + str(n) + ": bad float for total: " + repr(row[2])) from None
        rec["note"] = row[3] if row[3] != "" else None
        out.append(rec)
    if not header_seen:
        raise ValueError("orders: empty file")
    return out


def parse_products(text):
    if text.startswith("﻿"):
        text = text[1:]
    rd = csv.reader(text.splitlines())
    out = []
    header_seen = False
    for row in rd:
        if not row or all(c.strip() == "" for c in row):
            continue
        n = rd.line_num
        row = [c.strip() for c in row]
        if not header_seen:
            if row != ["sku", "price", "stock", "discontinued"]:
                raise ValueError("products: bad header " + repr(row))
            header_seen = True
            continue
        if len(row) != 4:
            raise ValueError("products line " + str(n) + ": expected 4 columns, got " + str(len(row)))
        rec = {}
        if row[0] == "":
            raise ValueError("products line " + str(n) + ": missing value for sku")
        rec["sku"] = row[0].upper()
        if row[1] == "":
            raise ValueError("products line " + str(n) + ": missing value for price")
        try:
            rec["price"] = float(row[1])
        except ValueError:
            raise ValueError("products line " + str(n) + ": bad float for price: " + repr(row[1])) from None
        if row[2] == "":
            rec["stock"] = 0
        else:
            try:
                rec["stock"] = int(row[2])
            except ValueError:
                raise ValueError("products line " + str(n) + ": bad int for stock: " + repr(row[2])) from None
        if row[3] == "":
            rec["discontinued"] = False
        else:
            v = row[3].lower()
            if v in ("yes", "true", "1"):
                rec["discontinued"] = True
            elif v in ("no", "false", "0"):
                rec["discontinued"] = False
            else:
                raise ValueError("products line " + str(n) + ": bad bool for discontinued: " + repr(row[3]))
        out.append(rec)
    if not header_seen:
        raise ValueError("products: empty file")
    return out
'''
TABLE_REF = r'''import csv

REQUIRED = object()


def _convert(kind, cell, col, where):
    if kind == "str":
        return cell
    if kind == "upper":
        return cell.upper()
    if kind == "email":
        if "@" not in cell:
            raise ValueError(f"{where}: bad email for {col}: {cell!r}")
        return cell.lower()
    if kind == "bool":
        v = cell.lower()
        if v in ("yes", "true", "1"):
            return True
        if v in ("no", "false", "0"):
            return False
        raise ValueError(f"{where}: bad bool for {col}: {cell!r}")
    try:
        return {"int": int, "float": float}[kind](cell)
    except ValueError:
        raise ValueError(f"{where}: bad {kind} for {col}: {cell!r}") from None


def parse_table(text, name, columns):
    """columns: list of (column, kind, default); kind in str/upper/email/int/float/bool; default REQUIRED means a blank is an error."""
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.reader(text.splitlines())
    names = [c[0] for c in columns]
    out, header_seen = [], False
    for row in reader:
        if not row or all(c.strip() == "" for c in row):
            continue
        where = f"{name} line {reader.line_num}"
        row = [c.strip() for c in row]
        if not header_seen:
            if row != names:
                raise ValueError(f"{name}: bad header {row!r}")
            header_seen = True
            continue
        if len(row) != len(columns):
            raise ValueError(f"{where}: expected {len(columns)} columns, got {len(row)}")
        rec = {}
        for cell, (col, kind, default) in zip(row, columns):
            if cell == "":
                if default is REQUIRED:
                    raise ValueError(f"{where}: missing value for {col}")
                rec[col] = default
            else:
                rec[col] = _convert(kind, cell, col, where)
        out.append(rec)
    if not header_seen:
        raise ValueError(f"{name}: empty file")
    return out
'''
LOADERS_REF = r'''from tablefmt import REQUIRED, parse_table

USERS = [("id", "int", REQUIRED), ("name", "str", REQUIRED), ("email", "email", REQUIRED), ("age", "int", None),
         ("active", "bool", True)]
ORDERS = [("order_id", "str", REQUIRED), ("user_id", "int", REQUIRED), ("total", "float", REQUIRED),
          ("note", "str", None)]
PRODUCTS = [("sku", "upper", REQUIRED), ("price", "float", REQUIRED), ("stock", "int", 0),
            ("discontinued", "bool", False)]


def parse_users(text):
    return parse_table(text, "users", USERS)


def parse_orders(text):
    return parse_table(text, "orders", ORDERS)


def parse_products(text):
    return parse_table(text, "products", PRODUCTS)
'''
LOADERS_TEST = r'''import ast, random
import loaders, tablefmt

ns = {}
exec(r"""''' + LOADERS_LEGACY.replace('"""', "'''") + r'''""", ns)


def outcome(fn, text):
    try:
        return ("ok", fn(text))
    except Exception as e:
        return ("err", type(e).__name__, str(e))


HEADERS = {"users": "id,name,email,age,active", "orders": "order_id,user_id,total,note", "products": "sku,price,stock,discontinued"}
CELLS = {"users": [["1", "7", "x", "", "-3", " 12 "], ["Ann", "", "Bob, Jr.", ' "Q ""x"" Z"'], ["A@X.io", "b@Y", "nope", "", " c@d.e "],
                   ["30", "", "x", "4.5", "-1"], ["yes", "No", "TRUE", "0", "", "maybe", "2"]],
         "orders": [["o1", "", "O-9", " ab "], ["1", "", "x", "07", "1.5"], ["9.99", "", "1e3", "abc", "-0.5", " 3 "], ["", "note", "a, b", '"multi'], []],
         "products": [["abc", "", "Zx-1", " q9 "], ["1.5", "", "x", "10", "0.0", "1e2"], ["3", "", "x", "-4", "2.5"], ["yes", "", "0", "no", "1", "huh"], []]}
rnd = random.Random(31)
for name in ("users", "orders", "products"):
    old, new = ns["parse_" + name], getattr(loaders, "parse_" + name)
    ncols = len(HEADERS[name].split(","))
    for _ in range(2500):
        lines = [] if rnd.random() < 0.03 else [rnd.choice([HEADERS[name], HEADERS[name], HEADERS[name], HEADERS[name].replace("_", " "), " " + HEADERS[name] + " "])]
        for _ in range(rnd.randint(0, 5)):
            r = rnd.random()
            if r < 0.1:
                lines.append(rnd.choice(["", "  ", ",,,,"]))
            else:
                cells = [rnd.choice(CELLS[name][i % len(CELLS[name])] or [""]) for i in range(ncols + (rnd.random() < 0.1) - (rnd.random() < 0.1))]
                lines.append(",".join(cells))
        text = ("﻿" if rnd.random() < 0.2 else "") + rnd.choice(["\n", "\r\n"]).join(lines) + rnd.choice(["", "\n"])
        assert outcome(new, text) == outcome(old, text), (name, text)

# exact structure
assert tablefmt.parse_table("a,b\n1,x\n", "t", [("a", "int", tablefmt.REQUIRED), ("b", "str", "d")]) == [{"a": 1, "b": "x"}]
assert tablefmt.parse_table("a,b\n1,\n", "t", [("a", "int", tablefmt.REQUIRED), ("b", "str", "d")]) == [{"a": 1, "b": "d"}]
try:
    tablefmt.parse_table("a,b\n,1\n", "t", [("a", "int", tablefmt.REQUIRED), ("b", "int", 0)])
except ValueError as e:
    assert str(e) == "t line 2: missing value for a"
else:
    raise AssertionError
src = open("loaders.py").read()
assert "csv" not in src, "loaders.py must delegate all parsing to tablefmt"
tree = ast.parse(src)
for n in tree.body:
    if isinstance(n, ast.FunctionDef):
        assert n.end_lineno - n.lineno + 1 <= 3, n.name
assert {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} >= {"parse_users", "parse_orders", "parse_products"}
assert "csv" in open("tablefmt.py").read()
print("ok")
'''
task("dev2-ref-table-loaders", "refactoring",
     "loaders.py has three copy-pasted CSV parsers (users, orders, products) that drift apart whenever one is fixed. "
     "Refactor without changing any behaviour (return values, error types and exact error messages, line numbers, "
     "BOM/blank-line/CRLF handling): create tablefmt.py with `REQUIRED` (a sentinel) and "
     "`parse_table(text, name, columns)` where columns is a list of (column_name, kind, default), kind one of "
     "str/upper/email/int/float/bool, and a blank cell yields `default` (or raises 'missing value' if the default is "
     "REQUIRED). Make parse_users/parse_orders/parse_products in loaders.py thin wrappers (no csv handling left in "
     "loaders.py, each at most 3 lines).",
     {"loaders.py": LOADERS_LEGACY}, {"hidden_t.py": LOADERS_TEST}, "python3 hidden_t.py",
     {"loaders.py": LOADERS_REF, "tablefmt.py": TABLE_REF}, split="dev")

# =====================================================================================================================
# TEST ENGINEERING (mutation graded)
# =====================================================================================================================
PAGER = r'''ELLIPSIS = "..."


def page_window(total, current, around=1, edge=1):
    """Page numbers for a pager. Always shows the first and last `edge` pages and `around` pages on each side of
    `current`. A hidden run of exactly one page is shown as that page; a longer run becomes one ELLIPSIS entry.
    `current` is clamped into 1..total; negative around/edge count as 0; total < 1 gives []."""
    if total < 1:
        return []
    current = min(max(current, 1), total)
    around = max(around, 0)
    edge = max(edge, 0)
    keep = set(range(1, min(edge, total) + 1))
    keep |= set(range(max(total - edge + 1, 1), total + 1))
    keep |= set(range(max(current - around, 1), min(current + around, total) + 1))
    out = []
    prev = 0
    for p in sorted(keep) + [total + 1]:
        gap = p - prev - 1
        if gap == 1:
            out.append(prev + 1)
        elif gap > 1:
            out.append(ELLIPSIS)
        if p <= total:
            out.append(p)
        prev = p
    return out
'''
PAGER_TEST = r'''from pager import page_window, ELLIPSIS as E

assert page_window(0, 1) == [] and page_window(-3, 1) == []
assert E == "..."
assert page_window(1, 1) == [1] and page_window(1, 99, 5, 5) == [1]
assert page_window(5, 3) == [1, 2, 3, 4, 5]
assert page_window(7, 4) == [1, 2, 3, 4, 5, 6, 7]                 # hidden runs of one page are shown
assert page_window(20, 10) == [1, E, 9, 10, 11, E, 20]
assert page_window(20, 1) == [1, 2, E, 20]
assert page_window(20, 2) == [1, 2, 3, E, 20]
assert page_window(20, 3) == [1, 2, 3, 4, E, 20]
assert page_window(20, 4) == [1, 2, 3, 4, 5, E, 20]
assert page_window(20, 5) == [1, E, 4, 5, 6, E, 20]
assert page_window(20, 20) == [1, E, 19, 20]
assert page_window(20, 19) == [1, E, 18, 19, 20]
assert page_window(20, 17) == [1, E, 16, 17, 18, 19, 20]
assert page_window(20, 16) == [1, E, 15, 16, 17, E, 20]
assert page_window(20, 0) == page_window(20, 1) and page_window(20, -5) == page_window(20, 1)
assert page_window(20, 25) == page_window(20, 20) and page_window(20, 21) == [1, E, 19, 20]
assert page_window(20, 10, around=0) == [1, E, 10, E, 20]
assert page_window(20, 10, around=-2) == [1, E, 10, E, 20]
assert page_window(20, 10, around=2) == [1, E, 8, 9, 10, 11, 12, E, 20]
assert page_window(20, 10, edge=0) == [E, 9, 10, 11, E]
assert page_window(20, 10, edge=-1) == [E, 9, 10, 11, E]
assert page_window(20, 10, edge=2) == [1, 2, E, 9, 10, 11, E, 19, 20]
assert page_window(20, 10, edge=3, around=1) == [1, 2, 3, E, 9, 10, 11, E, 18, 19, 20]
assert page_window(20, 5, edge=2) == [1, 2, 3, 4, 5, 6, E, 19, 20]
assert page_window(20, 6, edge=2) == [1, 2, E, 5, 6, 7, E, 19, 20]
assert page_window(20, 7, edge=2) == [1, 2, E, 6, 7, 8, E, 19, 20]
assert page_window(20, 2, edge=0) == [1, 2, 3, E]
assert page_window(20, 3, edge=0) == [E, 2, 3, 4, E] or page_window(20, 3, edge=0) == [1, 2, 3, 4, E]
assert page_window(4, 2, edge=10) == [1, 2, 3, 4] and page_window(3, 2, around=10) == [1, 2, 3]
assert page_window(2, 1, edge=0, around=0) == [1, 2]
assert page_window(10, 1, edge=0, around=0) == [1, E]
assert page_window(10, 10, edge=0, around=0) == [E, 10]
assert page_window(10, 9, edge=0, around=0) == [E, 9, 10]
assert page_window(10, 5, edge=0, around=0) == [E, 5, E]
print("ok")
'''
PAGER_MUT = {
    "no-clamp-low": ("current = min(max(current, 1), total)", "current = min(current, total)"),
    "no-clamp-high": ("current = min(max(current, 1), total)", "current = max(current, 1)"),
    "clamp-zero": ("current = min(max(current, 1), total)", "current = min(max(current, 0), total)"),
    "total-lt-2": ("if total < 1:", "if total < 2:"),
    "around-neg": ("around = max(around, 0)", "around = around"),
    "edge-head-short": ("keep = set(range(1, min(edge, total) + 1))", "keep = set(range(1, min(edge, total)))"),
    "edge-tail-off": ("range(max(total - edge + 1, 1), total + 1)", "range(max(total - edge, 1), total + 1)"),
    "around-low-off": ("range(max(current - around, 1), min(current + around, total) + 1)",
                       "range(max(current - around + 1, 1), min(current + around, total) + 1)"),
    "around-high-off": ("range(max(current - around, 1), min(current + around, total) + 1)",
                        "range(max(current - around, 1), min(current + around, total))"),
    "gap1-as-ellipsis": ("if gap == 1:", "if gap == 0:"),
    "gap-threshold": ("elif gap > 1:", "elif gap > 2:"),
    "gap1-wrong-page": ("out.append(prev + 1)", "out.append(prev)"),
    "no-trailing": ("for p in sorted(keep) + [total + 1]:", "for p in sorted(keep):"),
    "ellipsis-text": ('ELLIPSIS = "..."', 'ELLIPSIS = "\u2026"'),
}
mut_task("dev2-te-pager", "Write thorough tests for pager.page_window (read its docstring for the intended behaviour: "
         "first/last `edge` pages, `around` pages each side of the current one, a hidden run of exactly one page is "
         "shown as that page, longer runs collapse to one ELLIPSIS, clamping and negative arguments).",
         "pager.py", PAGER, "test_pager.py", PAGER_MUT, PAGER_TEST, split="dev")

TTL = r'''from collections import OrderedDict


class TTLCache:
    """LRU cache with per-entry expiry. `clock` is a zero-argument callable returning seconds.
    An entry written at time t with ttl expires AT t + ttl (it is already gone when clock() >= t + ttl).
    ttl=None (cache default or per put) means never expires; ttl <= 0 raises ValueError."""

    def __init__(self, capacity, ttl=None, clock=None):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self._d = OrderedDict()                      # key -> (value, expires_at or None), oldest first

    def _expired(self, key):
        exp = self._d[key][1]
        return exp is not None and self.clock() >= exp

    def _purge(self):
        for key in [k for k in self._d if self._expired(k)]:
            del self._d[key]

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        ttl = self.ttl if ttl is None else ttl
        exp = None if ttl is None else self.clock() + ttl
        if key in self._d:
            del self._d[key]
        elif len(self._d) >= self.capacity:
            self._purge()
            if len(self._d) >= self.capacity:
                self._d.popitem(last=False)
        self._d[key] = (value, exp)

    def get(self, key, default=None):
        if key not in self._d or self._expired(key):
            self._d.pop(key, None)
            return default
        self._d.move_to_end(key)
        return self._d[key][0]

    def peek(self, key, default=None):
        if key not in self._d or self._expired(key):
            self._d.pop(key, None)
            return default
        return self._d[key][0]

    def delete(self, key):
        if key not in self._d:
            return False
        live = not self._expired(key)
        del self._d[key]
        return live

    def keys(self):
        self._purge()
        return list(self._d)

    def __len__(self):
        self._purge()
        return len(self._d)
'''
TTL_TEST = r'''from ttlcache import TTLCache


class Clock:
    def __init__(self):
        self.t = 100.0
    def __call__(self):
        return self.t


def mk(cap=3, ttl=None):
    c = Clock()
    return TTLCache(cap, ttl, c), c


def raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    return False


assert raises(lambda: TTLCache(0, None, Clock())) and raises(lambda: TTLCache(-1, None, Clock()))
assert raises(lambda: TTLCache(1, 0, Clock())) and raises(lambda: TTLCache(1, -5, Clock()))
TTLCache(1, None, Clock()); TTLCache(1, 0.5, Clock())
cache, clk = mk(2, None)
assert raises(lambda: cache.put("k", 1, ttl=0)) and raises(lambda: cache.put("k", 1, ttl=-1)) and len(cache) == 0

# expiry boundary, default and per-put ttl
cache, clk = mk(5, 10)
cache.put("a", 1); cache.put("b", 2, ttl=3); cache.put("c", 3, ttl=100)
clk.t = 102.999
assert cache.get("b") == 2
clk.t = 103.0                                   # exactly at the expiry instant: gone
assert cache.get("b") is None and cache.get("b", "d") == "d" and cache.peek("b") is None
clk.t = 109.999
assert cache.get("a") == 1
clk.t = 110.0
assert cache.get("a") is None and cache.get("c") == 3 and len(cache) == 1 and cache.keys() == ["c"]
cache.put("n", 0, ttl=None)                     # None means the default ttl (10), not forever
clk.t = 120.0
assert cache.get("n") is None
c2, k2 = mk(3, None)
c2.put("x", 1); c2.put("y", 2, ttl=5)
k2.t += 1e9
assert c2.get("x") == 1 and c2.get("y") is None

# an update refreshes both the expiry and the recency
c3, k3 = mk(2, 10)
c3.put("a", 1); k3.t += 6; c3.put("a", 2)
k3.t += 6
assert c3.get("a") == 2
k3.t += 5
assert c3.get("a") is None
c3, k3 = mk(2, None)
c3.put("a", 1); c3.put("b", 2); c3.put("a", 3)
c3.put("c", 4)
assert c3.get("b") is None and c3.get("a") == 3 and c3.get("c") == 4 and len(c3) == 2

# LRU order: get refreshes, peek does not, put of existing key does not evict anything
c4, _ = mk(3, None)
for k in "abc":
    c4.put(k, k.upper())
assert c4.get("a") == "A" and c4.keys() == ["b", "c", "a"]
assert c4.peek("b") == "B" and c4.keys() == ["b", "c", "a"]
c4.put("d", "D")
assert c4.keys() == ["c", "a", "d"] and c4.get("b") is None
c4.put("a", "A2")
assert c4.keys() == ["c", "d", "a"] and len(c4) == 3
c4.put("e", "E")
assert c4.keys() == ["d", "a", "e"]
assert c4.get("zzz") is None and c4.get("zzz", 0) == 0 and c4.keys() == ["d", "a", "e"]

# capacity 1 and eviction prefers expired entries over live LRU ones
c5, k5 = mk(1, None)
c5.put("a", 1); c5.put("b", 2)
assert c5.keys() == ["b"] and len(c5) == 1
c6, k6 = mk(2, None)
c6.put("old", 1); c6.put("short", 2, ttl=5); c6.get("old")      # order: short, old; 'old' is the most recent
k6.t += 6                                                      # 'short' expired but is least recent anyway
c6.put("x", 3)
assert c6.keys() == ["old", "x"]
c7, k7 = mk(3, None)
c7.put("a", 1); c7.put("e", 2, ttl=5); c7.put("c", 3)           # LRU is 'a' (live); 'e' expired in the middle
k7.t += 6
c7.put("d", 4)
assert c7.keys() == ["a", "c", "d"], c7.keys()                   # expired 'e' was dropped, live LRU 'a' kept
c7.put("f", 5)
assert c7.keys() == ["c", "d", "f"]

# delete: True only for a live entry; expired entries are gone either way
c8, k8 = mk(3, 5)
c8.put("a", 1); c8.put("b", 2)
assert c8.delete("a") is True and c8.delete("a") is False and c8.delete("zz") is False
k8.t += 5
assert c8.delete("b") is False and len(c8) == 0 and c8.get("b") is None
# len and keys never count expired entries; get/peek of an expired key do not resurrect it
c9, k9 = mk(4, None)
c9.put("a", 1, ttl=2); c9.put("b", 2, ttl=4); c9.put("c", 3)
k9.t += 2
assert len(c9) == 2 and c9.keys() == ["b", "c"]
k9.t += 2
assert len(c9) == 1 and c9.peek("b") is None and c9.keys() == ["c"]
c10, k10 = mk(4, None)
c10.put("a", 1, ttl=2); c10.put("b", 2)
k10.t += 2
assert c10.keys() == ["b"]
print("ok")
'''
TTL_MUT = {
    "boundary-gt": ("return exp is not None and self.clock() >= exp", "return exp is not None and self.clock() > exp"),
    "expiry-ignored": ("return exp is not None and self.clock() >= exp", "return False"),
    "ttl-plus-one": ("exp = None if ttl is None else self.clock() + ttl", "exp = None if ttl is None else self.clock() + ttl + 1"),
    "put-ttl-ignored": ("ttl = self.ttl if ttl is None else ttl", "ttl = self.ttl"),
    "default-ttl-ignored": ("ttl = self.ttl if ttl is None else ttl", "ttl = ttl"),
    "cap-check-gt": ("elif len(self._d) >= self.capacity:", "elif len(self._d) > self.capacity:"),
    "inner-cap-gt": ("if len(self._d) >= self.capacity:\n                self._d.popitem(last=False)",
                     "if len(self._d) > self.capacity:\n                self._d.popitem(last=False)"),
    "no-purge-first": ("            self._purge()\n            if len(self._d) >= self.capacity:", "            if len(self._d) >= self.capacity:"),
    "evict-mru": ("self._d.popitem(last=False)", "self._d.popitem(last=True)"),
    "get-no-refresh": ("        self._d.move_to_end(key)\n        return self._d[key][0]", "        return self._d[key][0]"),
    "peek-refreshes": ("            return default\n        return self._d[key][0]\n\n    def delete", "            return default\n        self._d.move_to_end(key)\n        return self._d[key][0]\n\n    def delete"),
    "update-keeps-position": ("        if key in self._d:\n            del self._d[key]\n", "        if key in self._d:\n            self._d[key] = (value, exp)\n            return\n"),
    "delete-expired-true": ("        live = not self._expired(key)", "        live = True"),
    "delete-miss-true": ("        if key not in self._d:\n            return False", "        if key not in self._d:\n            return True"),
    "len-counts-expired": ("    def __len__(self):\n        self._purge()\n", "    def __len__(self):\n"),
    "keys-counts-expired": ("    def keys(self):\n        self._purge()\n", "    def keys(self):\n"),
    "cap-validation": ("if capacity < 1:", "if capacity < 0:"),
    "ttl-validation": ("if ttl is not None and ttl <= 0:\n            raise ValueError(\"ttl must be positive\")\n        self.capacity",
                       "if ttl is not None and ttl < 0:\n            raise ValueError(\"ttl must be positive\")\n        self.capacity"),
    "put-ttl-validation": ("        if ttl is not None and ttl <= 0:\n            raise ValueError(\"ttl must be positive\")\n        ttl = self.ttl",
                           "        if ttl is not None and ttl < 0:\n            raise ValueError(\"ttl must be positive\")\n        ttl = self.ttl"),
    "get-default-dropped": ("            self._d.pop(key, None)\n            return default\n        self._d.move_to_end", "            self._d.pop(key, None)\n            return None\n        self._d.move_to_end"),
}
mut_task("dev2-te-ttlcache", "Write thorough tests for ttlcache.TTLCache (see its docstring and code for the intended "
         "behaviour: expiry exactly at t+ttl, per-put ttl, LRU recency rules for get/peek/put, eviction preferring "
         "expired entries, delete/len/keys ignoring expired entries, argument validation). Use a fake clock object.",
         "ttlcache.py", TTL, "test_ttlcache.py", TTL_MUT, TTL_TEST, split="dev")
