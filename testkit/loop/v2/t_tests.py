"""Test-engineering tasks graded by mutation testing: the harness writes tests; they must pass on the correct module
and fail on every hidden mutant (each a plausible bug). Tests are plain Python files run with `python3 <file>`."""
import json

from common import task

RUNNER = r'''
import json, shutil, subprocess, sys
spec = json.load(open('.mutants.json'))
mod, test = spec['module'], spec['test']
import os
def run():
    shutil.rmtree('__pycache__', ignore_errors=True)           # never a stale compiled copy of the module
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, test], capture_output=True, text=True, timeout=60, env=env).returncode
orig = open(mod).read()
if run() != 0:
    print('tests fail on the correct code'); sys.exit(1)
survived = []
for name, src in spec['mutants'].items():
    open(mod, 'w').write(src)
    try:
        rc = run()
    except subprocess.TimeoutExpired:
        rc = 1
    if rc == 0:
        survived.append(name)
open(mod, 'w').write(orig)
if survived:
    print('mutants not caught:', survived); sys.exit(1)
print('ok')
'''


def mut_task(id, prompt, module, src, test_file, mutants, ref_tests, split="eval"):
    spec = {"module": module, "test": test_file, "mutants": {k: src.replace(a, b, 1) for k, (a, b) in mutants.items()}}
    for k, (a, b) in mutants.items():
        assert a in src and spec["mutants"][k] != src, (id, k)
    task(id, "test-engineering",
         prompt + f" Put the tests in {test_file} as a plain Python script (asserts; it must exit non-zero when a "
         f"test fails) runnable with `python3 {test_file}`. Do not change {module}.",
         {module: src}, {".mutants.json": json.dumps(spec), "hidden_mutate.py": RUNNER},
         "python3 hidden_mutate.py", {test_file: ref_tests}, split=split)


SLUG = "import unicodedata, re\n\ndef slugify(s, max_len=50, sep='-'):\n    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()\n    s = re.sub(r'[^a-z0-9]+', sep, s).strip(sep)\n    if len(s) > max_len:\n        cut = s[:max_len + 1]\n        s = cut[:cut.rfind(sep)] if sep in cut else s[:max_len]\n    return s or 'n-a'\n"
mut_task("te-slugify", "Write thorough tests for slug.slugify (see its code for the intended behaviour: accents "
         "folded to ASCII, any run of other characters becomes one separator, no leading or trailing separators, "
         "truncation only at a separator boundary when possible, 'n-a' for empty results).",
         "slug.py", SLUG, "test_slug.py",
         {"no_fold": (".encode('ascii', 'ignore').decode()", ""), "no_collapse": ("[^a-z0-9]+", "[^a-z0-9]"),
          "no_strip": (".strip(sep)", ""), "midword": ("cut[:cut.rfind(sep)] if sep in cut else s[:max_len]", "s[:max_len]"),
          "empty": ("s or 'n-a'", "s")},
         "from slug import slugify as f\nassert f('Héllo Wörld')=='hello-world'\nassert f('a  --  b')=='a-b'\nassert f('  !Hi!  ')=='hi'\nassert f('alpha beta gamma', max_len=12)=='alpha-beta'\nassert f('abcdefghij', max_len=4)=='abcd'\nassert f('!!!')=='n-a'\nassert f('A_B', sep='_')=='a_b'\nprint('ok')\n", split="dev")

SPLIT = "def split(amount_cents, n):\n    if n <= 0:\n        raise ValueError('n must be positive')\n    if amount_cents < 0:\n        raise ValueError('amount must not be negative')\n    q, r = divmod(amount_cents, n)\n    return [q + 1] * r + [q] * (n - r)\n"
mut_task("te-money-split", "Write thorough tests for money.split(amount_cents, n): it splits an amount into n parts "
         "that sum exactly to the amount, differ by at most one cent, with the larger parts first; n <= 0 and "
         "negative amounts raise ValueError.",
         "money.py", SPLIT, "test_money.py",
         {"rem_last": ("[q + 1] * r + [q] * (n - r)", "[q] * (n - r) + [q + 1] * r"), "drop_rem": ("[q + 1] * r + [q] * (n - r)", "[q] * n"),
          "n_zero": ("if n <= 0:", "if n < 0:"), "neg_ok": ("if amount_cents < 0:", "if amount_cents < -10**9:")},
         "from money import split\nassert split(100,3)==[34,33,33]\nassert split(10,4)==[3,3,2,2]\nassert split(0,2)==[0,0]\nassert sum(split(1001,7))==1001\nfor bad in ((5,0),(-1,2)):\n    try:\n        split(*bad); raise SystemExit(1)\n    except ValueError: pass\nprint('ok')\n")

SEMVER = "def _key(v):\n    v = v.split('+', 1)[0]\n    core, _, pre = v.partition('-')\n    nums = tuple(int(x) for x in core.split('.'))\n    if not pre:\n        return nums, (1,)\n    ids = tuple((0, int(p), '') if p.isdigit() else (1, 0, p) for p in pre.split('.'))\n    return nums, (0,) + ids\n\ndef compare(a, b):\n    ka, kb = _key(a), _key(b)\n    return (ka > kb) - (ka < kb)\n"
mut_task("te-semver", "Write thorough tests for semver.compare(a, b) (returns -1, 0 or 1 following Semantic Versioning "
         "2.0 precedence: numeric parts compared as numbers; a pre-release ranks below its release; pre-release "
         "identifiers compared numerically when numeric and lexically otherwise, numeric ones ranking lower; build "
         "metadata after '+' ignored).",
         "semver.py", SEMVER, "test_semver.py",
         {"str_nums": ("int(x) for x in core.split('.')", "x for x in core.split('.')"), "pre_high": ("return nums, (1,)", "return nums, (-1,)"),
          "pre_str": ("(0, int(p), '') if p.isdigit() else (1, 0, p)", "(1, 0, p)"), "build": ("v = v.split('+', 1)[0]", "v = v.replace('+', '.')")},
         "from semver import compare as c\nassert c('1.10.0','1.9.0')==1\nassert c('1.0.0-alpha','1.0.0')==-1\nassert c('1.0.0-alpha.2','1.0.0-alpha.10')==-1\nassert c('1.0.0-1','1.0.0-alpha')==-1\nassert c('1.0.0+build.5','1.0.0')==0\nassert c('2.0.0','2.0.0')==0\nprint('ok')\n")

BIZ = "from datetime import timedelta\n\ndef business_days(start, end, holidays=()):\n    if end <= start:\n        return 0\n    hol = set(holidays)\n    n, d = 0, start\n    while d < end:\n        if d.weekday() < 5 and d not in hol:\n            n += 1\n        d += timedelta(days=1)\n    return n\n"
mut_task("te-business-days", "Write thorough tests for dates.business_days(start, end, holidays): working days "
         "(Monday to Friday, excluding the given holiday dates) from start included to end excluded; 0 when end is "
         "not after start.",
         "dates.py", BIZ, "test_dates.py",
         {"incl_end": ("while d < end:", "while d <= end:"), "sat": ("d.weekday() < 5", "d.weekday() < 6"),
          "no_hol": ("and d not in hol", ""), "monday": ("d.weekday() < 5", "0 < d.weekday() < 5")},
         "from datetime import date\nfrom dates import business_days as b\nassert b(date(2026,3,2),date(2026,3,9))==5\nassert b(date(2026,3,6),date(2026,3,9))==1\nassert b(date(2026,3,7),date(2026,3,9))==0\nassert b(date(2026,3,2),date(2026,3,9),[date(2026,3,4)])==4\nassert b(date(2026,3,9),date(2026,3,2))==0\nassert b(date(2026,3,2),date(2026,3,2))==0\nprint('ok')\n")

ROUND = "from decimal import Decimal, ROUND_HALF_EVEN\n\ndef bankers_round(x, places=2):\n    q = Decimal(1).scaleb(-places)\n    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_EVEN))\n"
mut_task("te-bankers-round", "Write thorough tests for rounding.bankers_round(x, places): round half to even at the given "
         "number of decimal places, treating the input as the decimal number it prints as (2.675 is exactly 2.675, "
         "not its binary approximation).",
         "rounding.py", ROUND, "test_rounding.py",
         {"half_up": ("ROUND_HALF_EVEN))", "ROUND_HALF_UP))"), "float_in": ("Decimal(str(x))", "Decimal(x)"),
          "places": ("scaleb(-places)", "scaleb(-2)"), "builtin": ("return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_EVEN))", "return round(x + 1e-12, places)")},
         "from rounding import bankers_round as r\nassert r(2.5,0)==2.0 and r(3.5,0)==4.0\nassert r(2.675,2)==2.68\nassert r(0.125,2)==0.12 and r(0.135,2)==0.14\nassert r(-2.5,0)==-2.0\nassert r(1.0005,3)==1.0\nprint('ok')\n")

BUCKET = "class TokenBucket:\n    def __init__(self, rate, capacity, clock):\n        self.rate, self.capacity, self.clock = rate, capacity, clock\n        self.tokens = capacity\n        self.last = clock()\n\n    def allow(self, n=1):\n        now = self.clock()\n        if now > self.last:\n            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)\n            self.last = now\n        if self.tokens >= n:\n            self.tokens -= n\n            return True\n        return False\n"
mut_task("te-token-bucket", "Write thorough tests for bucket.TokenBucket(rate, capacity, clock).allow(n): tokens refill "
         "continuously at `rate` per second up to `capacity`; allow(n) takes n tokens and returns True if at least "
         "n are available, otherwise returns False and takes nothing; a clock that goes backwards adds nothing.",
         "bucket.py", BUCKET, "test_bucket.py",
         {"no_cap": ("min(self.capacity, self.tokens + (now - self.last) * self.rate)", "self.tokens + (now - self.last) * self.rate"),
          "int_refill": ("(now - self.last) * self.rate", "int(now - self.last) * self.rate"), "partial": ("if self.tokens >= n:", "if self.tokens > n - 1:"),
          "no_last": ("            self.last = now\n", "")},
         "from bucket import TokenBucket\nt=[0.0]\nb=TokenBucket(2,5,lambda:t[0])\nassert all(b.allow() for _ in range(5)) and not b.allow()\nt[0]=0.5; assert b.allow() and not b.allow()\nt[0]=100; assert b.allow(5) and not b.allow()\nt[0]=100.25; assert not b.allow()\nt[0]=100.75; assert b.allow() and not b.allow()\nt[0]=50; assert not b.allow()\nt[0]=101.0; assert b.allow() and not b.allow()\nprint('ok')\n")

CSVL = "def parse_line(line, delim=','):\n    out, cur, q, i = [], '', False, 0\n    while i < len(line):\n        ch = line[i]\n        if q:\n            if ch == '\"' and i + 1 < len(line) and line[i + 1] == '\"':\n                cur += '\"'; i += 1\n            elif ch == '\"':\n                q = False\n            else:\n                cur += ch\n        elif ch == '\"':\n            q = True\n        elif ch == delim:\n            out.append(cur); cur = ''\n        else:\n            cur += ch\n        i += 1\n    if q:\n        raise ValueError('unterminated quote')\n    out.append(cur)\n    return out\n"
mut_task("te-csv-line", "Write thorough tests for csvline.parse_line(line, delim): splits one CSV line into fields; "
         "fields may be quoted with \"; inside quotes the delimiter is literal and \"\" is one quote; an unterminated "
         "quote raises ValueError; an empty line is one empty field; the delimiter is configurable.",
         "csvline.py", CSVL, "test_csvline.py",
         {"no_escape": ("cur += '\"'; i += 1", "q = False"), "unterm": ("if q:\n        raise ValueError('unterminated quote')", "if False:\n        pass"),
          "delim": ("elif ch == delim:", "elif ch == ','"), "last": ("    out.append(cur)\n    return out", "    if cur:\n        out.append(cur)\n    return out")},
         "from csvline import parse_line as p\nassert p('a,b,c')==['a','b','c']\nassert p('\"x,y\",z')==['x,y','z']\nassert p('\"say \"\"hi\"\"\",2')==['say \"hi\"','2']\nassert p('')==['']\nassert p('a,')==['a','']\nassert p('a;b,c',';')==['a','b,c']\ntry:\n    p('\"open'); raise SystemExit(1)\nexcept ValueError: pass\nprint('ok')\n")
