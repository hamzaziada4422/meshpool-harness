from common import task

# 1 (dev) memoize ignores keyword arguments; the pricing module calls with keywords
task("dbg-memo-kwargs", "debugging",
     "Customers sometimes get another customer's price. pricing.quote() is wrong after the first call with a "
     "different currency or discount. Find the root cause and fix it so every call returns the right price.",
     {"memo.py": "import functools\n\ndef memoize(fn):\n    cache = {}\n    @functools.wraps(fn)\n    def wrap(*args, **kwargs):\n        if args not in cache:\n            cache[args] = fn(*args, **kwargs)\n        return cache[args]\n    wrap.cache = cache\n    return wrap\n",
      "pricing.py": "from memo import memoize\n\nRATES = {'USD': 1.0, 'EUR': 0.9, 'GBP': 0.8}\n\n@memoize\ndef quote(product_price, currency='USD', discount=0.0):\n    return round(product_price * RATES[currency] * (1 - discount), 2)\n"},
     {"hidden_t.py": "from pricing import quote\nassert quote(100) == 100.0\nassert quote(100, currency='EUR') == 90.0\nassert quote(100, currency='EUR', discount=0.5) == 45.0\nassert quote(100, discount=0.1) == 90.0\nassert quote(100, 'GBP') == 80.0 and quote(100, currency='GBP') == 80.0\nimport memo\n@memo.memoize\ndef f(a, b=1, *, c=2): return (a, b, c)\nassert f(1) == (1, 1, 2) and f(1, c=5) == (1, 1, 5) and f(1, b=3) == (1, 3, 2) and f(1, 3) == (1, 3, 2)\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"memo.py": "import functools\n\ndef memoize(fn):\n    cache = {}\n    @functools.wraps(fn)\n    def wrap(*args, **kwargs):\n        key = (args, tuple(sorted(kwargs.items())))\n        if key not in cache:\n            cache[key] = fn(*args, **kwargs)\n        return cache[key]\n    wrap.cache = cache\n    return wrap\n"},
     split="dev")

# 2 time zones across modules: grouping by naive UTC date
task("dbg-tz-daily", "debugging",
     "The daily sales report puts late-evening sales on the wrong day for our Dubai store (UTC+4). Events are stored "
     "with UTC timestamps; the report must group by the store's local calendar day. Fix it without changing how "
     "events are stored.",
     {"store.py": "STORES = {'dubai': 4, 'london': 0, 'nyc': -5}  # UTC offset in hours\n",
      "events.py": "from datetime import datetime\n\ndef parse(ts):\n    # timestamps are ISO strings in UTC, e.g. 2026-03-01T21:30:00Z\n    return datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ')\n",
      "report.py": "from collections import defaultdict\nfrom events import parse\nfrom store import STORES\n\ndef daily_totals(store, sales):\n    out = defaultdict(float)\n    for ts, amount in sales:\n        day = parse(ts).date().isoformat()\n        out[day] += amount\n    return dict(out)\n"},
     {"hidden_t.py": "from report import daily_totals\nsales=[('2026-03-01T19:59:59Z',10),('2026-03-01T20:00:00Z',5),('2026-03-01T23:30:00Z',1),('2026-02-28T22:00:00Z',2)]\nassert daily_totals('dubai', sales) == {'2026-03-01': 12.0, '2026-03-02': 6.0}, daily_totals('dubai', sales)\nassert daily_totals('london', sales) == {'2026-03-01': 16.0, '2026-02-28': 2.0}\nassert daily_totals('nyc', [('2026-03-02T03:00:00Z', 7)]) == {'2026-03-01': 7.0}\nfrom events import parse\nassert parse('2026-03-01T21:30:00Z').hour == 21\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"report.py": "from collections import defaultdict\nfrom datetime import timedelta\nfrom events import parse\nfrom store import STORES\n\ndef daily_totals(store, sales):\n    out = defaultdict(float)\n    for ts, amount in sales:\n        day = (parse(ts) + timedelta(hours=STORES[store])).date().isoformat()\n        out[day] += amount\n    return dict(out)\n"})

# 3 class-level mutable state shared across instances
task("dbg-shared-registry", "debugging",
     "Two independent plugin registries interfere: registering a plugin in one shows up in the other, and "
     "Registry.copy() returns a registry whose changes leak back into the original. Fix it.",
     {"registry.py": "class Registry:\n    plugins = {}\n    order = []\n\n    def register(self, name, fn):\n        if name in self.plugins:\n            raise ValueError(f'duplicate plugin {name}')\n        self.plugins[name] = fn\n        self.order.append(name)\n\n    def names(self):\n        return list(self.order)\n\n    def copy(self):\n        r = Registry()\n        r.plugins = self.plugins\n        r.order = self.order\n        return r\n"},
     {"hidden_t.py": "from registry import Registry\na,b=Registry(),Registry()\na.register('x',1); b.register('x',2)\nassert a.names()==['x'] and b.names()==['x'] and a.plugins['x']==1 and b.plugins['x']==2\nc=a.copy(); c.register('y',3)\nassert a.names()==['x'] and c.names()==['x','y']\ntry:\n    a.register('x',9); raise SystemExit('dup allowed')\nexcept ValueError: pass\nassert Registry().names()==[]\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"registry.py": "class Registry:\n    def __init__(self):\n        self.plugins = {}\n        self.order = []\n\n    def register(self, name, fn):\n        if name in self.plugins:\n            raise ValueError(f'duplicate plugin {name}')\n        self.plugins[name] = fn\n        self.order.append(name)\n\n    def names(self):\n        return list(self.order)\n\n    def copy(self):\n        r = Registry()\n        r.plugins = dict(self.plugins)\n        r.order = list(self.order)\n        return r\n"})

# 4 asyncio lost update
task("dbg-async-lost-update", "debugging",
     "Under load the wallet balance ends up too high or too low: concurrent deposits and withdrawals lose updates, "
     "and a withdrawal sometimes succeeds even though two concurrent ones together exceed the balance. Keep the "
     "async API and the simulated I/O delay; make the operations correct under concurrency.",
     {"wallet.py": "import asyncio\n\nclass InsufficientFunds(Exception):\n    pass\n\nclass Wallet:\n    def __init__(self, balance=0):\n        self.balance = balance\n\n    async def _io(self):\n        await asyncio.sleep(0.001)  # simulated database round trip\n\n    async def deposit(self, amount):\n        b = self.balance\n        await self._io()\n        self.balance = b + amount\n\n    async def withdraw(self, amount):\n        b = self.balance\n        await self._io()\n        if b < amount:\n            raise InsufficientFunds(amount)\n        self.balance = b - amount\n"},
     {"hidden_t.py": "import asyncio\nfrom wallet import Wallet, InsufficientFunds\nasync def main():\n    w=Wallet(0)\n    await asyncio.gather(*[w.deposit(1) for _ in range(200)])\n    assert w.balance==200, w.balance\n    w=Wallet(100)\n    res=await asyncio.gather(*[w.withdraw(60), w.withdraw(60)], return_exceptions=True)\n    assert sum(isinstance(r, InsufficientFunds) for r in res)==1 and w.balance==40, (res, w.balance)\n    w=Wallet(50)\n    await asyncio.gather(*[w.deposit(10) for _ in range(10)], *[w.withdraw(5) for _ in range(10)])\n    assert w.balance==100, w.balance\n    import inspect; assert inspect.iscoroutinefunction(Wallet.deposit)\nasyncio.run(main()); print('ok')\n"},
     "python3 hidden_t.py",
     {"wallet.py": "import asyncio\n\nclass InsufficientFunds(Exception):\n    pass\n\nclass Wallet:\n    def __init__(self, balance=0):\n        self.balance = balance\n        self._lock = asyncio.Lock()\n\n    async def _io(self):\n        await asyncio.sleep(0.001)\n\n    async def deposit(self, amount):\n        async with self._lock:\n            b = self.balance\n            await self._io()\n            self.balance = b + amount\n\n    async def withdraw(self, amount):\n        async with self._lock:\n            b = self.balance\n            await self._io()\n            if b < amount:\n                raise InsufficientFunds(amount)\n            self.balance = b - amount\n"})

# 5 generator consumed twice across modules
task("dbg-generator-reuse", "debugging",
     "summary() reports a max of None and a count of 0 for real data, but the mean is right. Fix the root cause; "
     "readings must still be streamed from the file (do not read the whole file into a list in reader.py: other "
     "callers rely on it being lazy).",
     {"reader.py": "def readings(path):\n    with open(path) as f:\n        for line in f:\n            line = line.strip()\n            if line and not line.startswith('#'):\n                yield float(line)\n",
      "stats.py": "def mean(xs):\n    total = n = 0\n    for x in xs:\n        total += x\n        n += 1\n    return total / n if n else None\n\ndef maximum(xs):\n    m = None\n    for x in xs:\n        m = x if m is None or x > m else m\n    return m\n\ndef count(xs):\n    return sum(1 for _ in xs)\n",
      "summary.py": "from reader import readings\nfrom stats import mean, maximum, count\n\ndef summary(path):\n    data = readings(path)\n    return {'mean': mean(data), 'max': maximum(data), 'count': count(data)}\n"},
     {"hidden_t.py": "import tempfile, os, types\nfrom summary import summary\nfrom reader import readings\np=os.path.join(tempfile.mkdtemp(),'r.txt'); open(p,'w').write('# header\\n1\\n5\\n\\n3\\n')\nassert summary(p)=={'mean':3.0,'max':5.0,'count':3}, summary(p)\nassert isinstance(readings(p), types.GeneratorType)\nq=os.path.join(tempfile.mkdtemp(),'e.txt'); open(q,'w').write('# none\\n')\nassert summary(q)=={'mean':None,'max':None,'count':0}\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"summary.py": "from reader import readings\nfrom stats import mean, maximum, count\n\ndef summary(path):\n    data = list(readings(path))\n    return {'mean': mean(data), 'max': maximum(data), 'count': count(data)}\n"})

# 6 CSV with quoted commas and a BOM
task("dbg-csv-import", "debugging",
     "Importing contacts.csv exported from Excel fails with KeyError: 'name', and once that is worked around some "
     "contacts get the wrong email. Fix the importer so it handles real exported files.",
     {"importer.py": "def load_contacts(path):\n    with open(path, encoding='utf-8') as f:\n        header = f.readline().strip().split(',')\n        out = []\n        for line in f:\n            if not line.strip():\n                continue\n            row = dict(zip(header, line.strip().split(',')))\n            out.append({'name': row['name'], 'email': row['email'].lower()})\n        return out\n"},
     {"hidden_t.py": "import tempfile, os\nfrom importer import load_contacts\np=os.path.join(tempfile.mkdtemp(),'c.csv')\nopen(p,'w',encoding='utf-8-sig',newline='').write('name,email,company\\r\\n\"Doe, Jane\",JANE@X.COM,\"Acme, Inc\"\\r\\nBob,bob@y.org,Y\\r\\n\\r\\n\"Ann \"\"A\"\" Lee\",ann@z.io,\"Z\"\\r\\n')\nr=load_contacts(p)\nassert r==[{'name':'Doe, Jane','email':'jane@x.com'},{'name':'Bob','email':'bob@y.org'},{'name':'Ann \"A\" Lee','email':'ann@z.io'}], r\nq=os.path.join(tempfile.mkdtemp(),'d.csv'); open(q,'w').write('name,email\\nA,a@b.c\\n'); assert load_contacts(q)==[{'name':'A','email':'a@b.c'}]\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"importer.py": "import csv\n\ndef load_contacts(path):\n    with open(path, encoding='utf-8-sig', newline='') as f:\n        out = []\n        for row in csv.DictReader(f):\n            if not row.get('name') and not row.get('email'):\n                continue\n            out.append({'name': row['name'], 'email': row['email'].lower()})\n        return out\n"})

# 7 retry decorator: swallows the final error, wrong backoff, retries non-retryable errors
task("dbg-retry", "debugging",
     "Our retry helper hides failures: after the last attempt callers get None instead of the error, the waits do "
     "not double as documented, and it even retries ValueError (a bug in the caller, which must fail at once). "
     "Fix retry() to match its docstring.",
     {"retry.py": "import time\n\nclass TransientError(Exception):\n    pass\n\ndef retry(fn, attempts=3, base_delay=0.1, sleep=time.sleep):\n    \"\"\"Call fn(); on TransientError wait base_delay, 2*base_delay, 4*base_delay ... between attempts and try\n    again, up to `attempts` calls in total. Any other exception propagates at once. After the last failed\n    attempt the last TransientError propagates.\"\"\"\n    delay = base_delay\n    for i in range(attempts):\n        try:\n            return fn()\n        except Exception:\n            sleep(delay)\n            delay += base_delay\n    return None\n"},
     {"hidden_t.py": "from retry import retry, TransientError\nwaits=[]\ncalls={'n':0}\ndef flaky():\n    calls['n']+=1\n    if calls['n']<3: raise TransientError(calls['n'])\n    return 'ok'\nassert retry(flaky, attempts=5, base_delay=1, sleep=waits.append)=='ok' and waits==[1,2] and calls['n']==3, (waits, calls)\nwaits.clear()\ndef always(): raise TransientError('x')\ntry:\n    retry(always, attempts=3, base_delay=0.5, sleep=waits.append); raise SystemExit('no raise')\nexcept TransientError: pass\nassert waits==[0.5,1.0], waits\nn={'c':0}\ndef bad():\n    n['c']+=1; raise ValueError('bug')\ntry:\n    retry(bad, sleep=waits.append); raise SystemExit('no raise')\nexcept ValueError: pass\nassert n['c']==1\nassert retry(lambda: 0, sleep=waits.append)==0\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"retry.py": "import time\n\nclass TransientError(Exception):\n    pass\n\ndef retry(fn, attempts=3, base_delay=0.1, sleep=time.sleep):\n    \"\"\"see the task\"\"\"\n    delay = base_delay\n    for i in range(attempts):\n        try:\n            return fn()\n        except TransientError:\n            if i == attempts - 1:\n                raise\n            sleep(delay)\n            delay *= 2\n"})

# 8 pagination: server overlaps pages, client dedupes by the wrong key and drops the last item
task("dbg-pagination-sync", "debugging",
     "Syncing the full product catalogue through the paged API sometimes skips products and sometimes duplicates "
     "them. Both the server (api.py) and the client (sync.py) may be involved. Fix it so fetch_all() returns every "
     "product exactly once, in id order, for any page size.",
     {"api.py": "PRODUCTS = [{'id': i, 'sku': f'S{i % 7}', 'name': f'p{i}'} for i in range(1, 52)]\n\ndef page(after_id=0, size=10):\n    items = [p for p in PRODUCTS if p['id'] >= after_id][:size]\n    return {'items': items, 'more': len(items) == size}\n",
      "sync.py": "import api\n\ndef fetch_all(size=10):\n    seen, out, after = set(), [], 0\n    while True:\n        r = api.page(after, size)\n        for p in r['items']:\n            if p['sku'] not in seen:\n                seen.add(p['sku'])\n                out.append(p)\n        if not r['more']:\n            break\n        after = r['items'][-1]['id']\n    return out\n"},
     {"hidden_t.py": "import api, sync\nfor size in (1,3,7,10,51,52,100):\n    got=[p['id'] for p in sync.fetch_all(size)]\n    assert got==list(range(1,52)), (size, got[:12])\napi.PRODUCTS=[]\nassert sync.fetch_all(5)==[]\napi.PRODUCTS=[{'id':i,'sku':'A','name':'x'} for i in range(1,21)]\nassert [p['id'] for p in sync.fetch_all(10)]==list(range(1,21))\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"api.py": "PRODUCTS = [{'id': i, 'sku': f'S{i % 7}', 'name': f'p{i}'} for i in range(1, 52)]\n\ndef page(after_id=0, size=10):\n    items = [p for p in PRODUCTS if p['id'] > after_id][:size]\n    return {'items': items, 'more': len(items) == size}\n",
      "sync.py": "import api\n\ndef fetch_all(size=10):\n    seen, out, after = set(), [], 0\n    while True:\n        r = api.page(after, size)\n        for p in r['items']:\n            if p['id'] not in seen:\n                seen.add(p['id'])\n                out.append(p)\n        if not r['items'] or not r['more']:\n            break\n        after = r['items'][-1]['id']\n    return sorted(out, key=lambda p: p['id'])\n"})

# 9 invalidation: a cache keyed on an object that mutates
task("dbg-stale-cache", "debugging",
     "After a user changes their plan, permissions still reflect the old plan until the app restarts. Fix the stale "
     "permissions without removing caching (permission checks are hot).",
     {"users.py": "class User:\n    def __init__(self, uid, plan):\n        self.uid = uid\n        self.plan = plan\n\n    def change_plan(self, plan):\n        self.plan = plan\n",
      "perms.py": "PLANS = {'free': {'read'}, 'pro': {'read', 'write'}, 'team': {'read', 'write', 'admin'}}\n_cache = {}\nCALLS = {'n': 0}\n\ndef _compute(plan):\n    CALLS['n'] += 1\n    return frozenset(PLANS[plan])\n\ndef permissions(user):\n    if user.uid not in _cache:\n        _cache[user.uid] = _compute(user.plan)\n    return _cache[user.uid]\n\ndef can(user, action):\n    return action in permissions(user)\n"},
     {"hidden_t.py": "from users import User\nimport perms\nu=User(1,'free')\nassert not perms.can(u,'write')\nu.change_plan('pro'); assert perms.can(u,'write') and not perms.can(u,'admin')\nu.change_plan('team'); assert perms.can(u,'admin')\nu.change_plan('free'); assert not perms.can(u,'write')\nv=User(2,'pro'); perms.CALLS['n']=0\nfor _ in range(1000): perms.can(v,'write')\nassert perms.CALLS['n']<=1, perms.CALLS['n']\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"perms.py": "PLANS = {'free': {'read'}, 'pro': {'read', 'write'}, 'team': {'read', 'write', 'admin'}}\n_cache = {}\nCALLS = {'n': 0}\n\ndef _compute(plan):\n    CALLS['n'] += 1\n    return frozenset(PLANS[plan])\n\ndef permissions(user):\n    key = (user.uid, user.plan)\n    if key not in _cache:\n        _cache[key] = _compute(user.plan)\n    return _cache[key]\n\ndef can(user, action):\n    return action in permissions(user)\n"})
