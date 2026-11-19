"""Data analysis tasks: realistic files with inconsistencies; the answer is computed here by plain Python (the
reference) and checked in the reply by pattern. Answers must state the number; a wrong one alongside the right one
fails the 'must_not' list where a likely wrong answer is known."""
import csv
import io
import random
from datetime import datetime, timedelta, timezone

from common import TASKS


def data_task(id, prompt, files, must, must_not, answer, split="eval"):
    TASKS.append({"id": f"v2-{id}", "category": "data", "difficulty": "hard", "split": split, "mode": "work",
                  "prompt": prompt, "files": files, "ref": {"__answer__": answer},
                  "grade": {"must": must, "must_not": must_not, "max_words": 400}})


def to_csv(header, rows):
    b = io.StringIO()
    w = csv.writer(b, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return b.getvalue()


def money(x):
    return f"{x:,.2f}".replace(",", ",?")


# 1 (dev) duplicates + currencies
r = random.Random(11)
rates = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}
rows, seen = [], []
for i in range(400):
    cur = r.choice(list(rates))
    amt = round(r.uniform(5, 500), 2)
    rows.append([f"O{1000 + i}", cur, amt])
dupes = [list(rows[r.randrange(400)]) for _ in range(37)]
allrows = rows + dupes
r.shuffle(allrows)
total = sum(a * rates[c] for _, c, a in rows)
naive = sum(a for _, _, a in allrows)
data_task("data-dupes-fx",
          "What was our total revenue in USD in orders.csv? Amounts are in the order's currency; rates.csv gives USD "
          "per unit of each currency. Give the total to the cent.",
          {"orders.csv": to_csv(["order_id", "currency", "amount"], allrows),
           "rates.csv": to_csv(["currency", "usd_per_unit"], [[k, v] for k, v in rates.items()])},
          [money(round(total, 2))], [money(round(naive, 2))], f"Total revenue: ${total:,.2f}", split="dev")

# 2 mixed timestamp formats and time zones; events per local day
r = random.Random(12)
base = datetime(2026, 3, 1, tzinfo=timezone.utc)
evs, count = [], 0
for i in range(500):
    t = base + timedelta(minutes=r.randint(0, 3 * 24 * 60))
    fmt = r.choice(["iso_z", "iso_off", "epoch", "us"])
    if fmt == "iso_z":
        s = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif fmt == "iso_off":
        s = t.astimezone(timezone(timedelta(hours=2))).isoformat()
    elif fmt == "epoch":
        s = str(int(t.timestamp()))
    else:
        s = t.strftime("%m/%d/%Y %H:%M UTC")
    evs.append([i, s])
    if (t + timedelta(hours=-5)).date().isoformat() == "2026-03-02":
        count += 1
data_task("data-mixed-timestamps",
          "events.csv logs signups from several systems with different timestamp formats (all of them identify an "
          "exact instant). How many signups happened on 2 March 2026 in New York local time (UTC-5 that week)? Give "
          "the number.",
          {"events.csv": to_csv(["event_id", "timestamp"], evs)},
          [rf"(?<![\d.]){count}(?![\d.])"], [], f"{count} signups")

# 3 join with inconsistent ids
r = random.Random(13)
segs = ["SMB", "Mid-Market", "Enterprise"]
cust = [[f"C{n:04d}", r.choice(segs)] for n in range(1, 151)]
orders, seg_rev = [], {s: 0.0 for s in segs}
for i in range(900):
    n = r.randint(1, 150)
    cid = r.choice([f"C{n:04d}", f"c{n:04d}", str(n), f"C-{n}", f" C{n:04d} "])
    amt = round(r.uniform(10, 900), 2)
    orders.append([i, cid, amt])
    seg_rev[cust[n - 1][1]] += amt
top = max(seg_rev, key=seg_rev.get)
data_task("data-messy-join",
          "Which customer segment brought in the most revenue in orders.csv, and how much? Segments are in "
          "customers.csv. Give the segment and the amount to the cent.",
          {"customers.csv": to_csv(["customer_id", "segment"], cust), "orders.csv": to_csv(["order_id", "customer", "amount"], orders)},
          [top, money(round(seg_rev[top], 2))], [], f"{top}: {seg_rev[top]:,.2f}")

# 4 units and sensor errors
r = random.Random(14)
rd, vals = [], []
for i in range(600):
    c = round(r.uniform(15, 30), 2)
    unit = r.choice(["C", "F", "K"])
    v = c if unit == "C" else (c * 9 / 5 + 32 if unit == "F" else c + 273.15)
    if r.random() < 0.04:
        rd.append([i, "-999", unit])
        continue
    rd.append([i, f"{v:.3f}", unit])
    vals.append(float(f"{v:.3f}") if unit == "C" else ((float(f"{v:.3f}") - 32) * 5 / 9 if unit == "F" else float(f"{v:.3f}") - 273.15))
mean_c = sum(vals) / len(vals)
data_task("data-sensor-units",
          "readings.csv has temperature readings in mixed units (C, F, K). -999 means the sensor failed. What is the "
          "mean temperature in Celsius over valid readings, to 2 decimal places?",
          {"readings.csv": to_csv(["reading_id", "value", "unit"], rd)},
          [rf"{mean_c:.2f}"], [], f"{mean_c:.2f} C")

# 5 cohort retention
r = random.Random(15)
acts, cohort, retained = [], set(), set()
for u in range(1, 401):
    start = datetime(2026, r.choice([1, 2]), r.randint(1, 28))
    acts.append([u, start.strftime("%Y-%m-%d"), "signup"])
    if start.month == 1:
        cohort.add(u)
    for _ in range(r.randint(0, 6)):
        d = start + timedelta(days=r.randint(1, 100))
        acts.append([u, d.strftime("%Y-%m-%d"), "active"])
        if start.month == 1 and d.year == 2026 and d.month == 3:
            retained.add(u)
r.shuffle(acts)
rate = 100 * len(retained) / len(cohort)
data_task("data-cohort-retention",
          "From activity.csv: of the users who signed up in January 2026, what percentage were active at least once "
          "in March 2026? One decimal place.",
          {"activity.csv": to_csv(["user_id", "date", "kind"], acts)},
          [rf"{rate:.1f}\s*%"], [], f"{rate:.1f}%")

# 6 Simpson's paradox
rows6 = []
plan = {("A", "mobile"): (900, 90), ("A", "desktop"): (100, 45), ("B", "mobile"): (200, 16), ("B", "desktop"): (800, 340)}
for (var, dev), (n, conv) in plan.items():
    rows6 += [[var, dev, 1 if i < conv else 0] for i in range(n)]
random.Random(16).shuffle(rows6)
data_task("data-simpson",
          "ab_test.csv has our checkout experiment (variant, device, converted). Which variant should we ship if we "
          "want the better conversion rate for users on every device? Answer with the variant letter first, then a "
          "one-sentence reason with the per-device rates.",
          {"ab_test.csv": to_csv(["variant", "device", "converted"], rows6)},
          [r"^\W*(?:\*\*)?(?:variant\s+)?A\b"], [r"^\W*(?:\*\*)?(?:variant\s+)?B\b"],
          "A. It converts better on mobile (10.0% vs 8.0%) and on desktop (45.0% vs 42.5%).")

# 7 CAGR with missing months
months, rev = [], {}
r = random.Random(17)
v = 10000.0
for y in (2023, 2024, 2025):
    for m in range(1, 13):
        v *= 1 + r.uniform(-0.02, 0.05)
        rev[(y, m)] = round(v, 2)
miss = r.sample(sorted(rev)[1:-1], 5)
for k in sorted(rev):
    if k not in miss:
        months.append([f"{k[0]}-{k[1]:02d}", rev[k]])
r.shuffle(months)
first, last = rev[(2023, 1)], rev[(2025, 12)]
cagr = ((last / first) ** (12 / 35) - 1) * 100
data_task("data-cagr-gaps",
          "monthly_revenue.csv has monthly revenue from January 2023 to December 2025, but some months are missing "
          "rows. What is the compound annual growth rate between January 2023 and December 2025, treating that as 35 "
          "months (CAGR = (last/first)^(12/35) - 1)? One decimal place.",
          {"monthly_revenue.csv": to_csv(["month", "revenue"], months)},
          [rf"{cagr:.1f}\s*%"], [], f"{cagr:.1f}%")
