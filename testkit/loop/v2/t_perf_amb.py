from common import task

# ---------- performance ----------------------------------------------------------------------------------------------
task("perf-anagrams", "performance",
     "group_anagrams(words) is far too slow on our dictionary (200k words). Make it fast; the output must stay exactly "
     "the same (groups of words that are anagrams of each other, ignoring case, each group in input order, groups "
     "ordered by their first word's position, single words included).",
     {"anagrams.py": "def group_anagrams(words):\n    groups, used = [], set()\n    for i, w in enumerate(words):\n        if i in used:\n            continue\n        g = [w]\n        for j in range(i + 1, len(words)):\n            if j not in used and sorted(words[j].lower()) == sorted(w.lower()):\n                g.append(words[j]); used.add(j)\n        groups.append(g)\n    return groups\n"},
     {"hidden_t.py": "import random, time, string\nfrom anagrams import group_anagrams as g\nassert g(['Tea','eat','tan','ate','Nat','bat'])==[['Tea','eat','ate'],['tan','Nat'],['bat']]\nassert g([])==[] and g(['a','A','a'])==[['a','A','a']]\nrnd=random.Random(2)\nws=[''.join(rnd.choice('abcdefg') for _ in range(rnd.randint(3,7))) for _ in range(200000)]\nt=time.time(); r=g(ws); dt=time.time()-t\nassert sum(len(x) for x in r)==200000 and dt<4, dt\nassert r[0][0]==ws[0]\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"anagrams.py": "def group_anagrams(words):\n    groups = {}\n    for w in words:\n        groups.setdefault(''.join(sorted(w.lower())), []).append(w)\n    return list(groups.values())\n"},
     split="dev", timeout=240)

task("perf-n-plus-one", "performance",
     "The order summary page takes seconds to load. report.build(order_ids) makes one database round trip per order "
     "and per customer. Make it fast using what db.py offers, with identical output.",
     {"db.py": "import time\nORDERS = {i: {'id': i, 'customer': i % 50, 'total': i * 1.5} for i in range(1, 2001)}\nCUSTOMERS = {c: {'id': c, 'name': f'cust{c}'} for c in range(50)}\nCALLS = {'n': 0}\n\ndef _trip():\n    CALLS['n'] += 1\n    time.sleep(0.002)\n\ndef get_order(i):\n    _trip(); return dict(ORDERS[i])\n\ndef get_orders(ids):\n    _trip(); return [dict(ORDERS[i]) for i in ids if i in ORDERS]\n\ndef get_customer(c):\n    _trip(); return dict(CUSTOMERS[c])\n\ndef get_customers(ids):\n    _trip(); return {c: dict(CUSTOMERS[c]) for c in set(ids) if c in CUSTOMERS}\n",
      "report.py": "import db\n\ndef build(order_ids):\n    rows = []\n    for i in order_ids:\n        o = db.get_order(i)\n        c = db.get_customer(o['customer'])\n        rows.append(f\"{o['id']}: {c['name']} {o['total']:.2f}\")\n    return rows\n"},
     {"hidden_t.py": "import time, db, report\nids=list(range(1,2001))[::-1][:1500]+[7,7]\nexp=[f\"{i}: cust{i%50} {i*1.5:.2f}\" for i in ids]\ndb.CALLS['n']=0; t=time.time(); r=report.build(ids); dt=time.time()-t\nassert r==exp, r[:3]\nassert db.CALLS['n']<=4 and dt<1.0, (db.CALLS['n'], dt)\nassert report.build([])==[]\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"report.py": "import db\n\ndef build(order_ids):\n    if not order_ids:\n        return []\n    orders = {o['id']: o for o in db.get_orders(list(dict.fromkeys(order_ids)))}\n    custs = db.get_customers([o['customer'] for o in orders.values()])\n    return [f\"{i}: {custs[orders[i]['customer']]['name']} {orders[i]['total']:.2f}\" for i in order_ids]\n"})

task("perf-regex-redos", "performance",
     "A user reported that submitting a long tag list hangs the server for minutes. validate_tags(s) uses a regex. "
     "Make it run in linear time on any input while accepting and rejecting exactly the same strings as the "
     "documented rule: one or more tags separated by single commas; a tag is 1-20 characters of letters, digits or "
     "'-', not starting or ending with '-'.",
     {"tags.py": "import re\nTAGS = re.compile(r'^(([a-z0-9]+-?)+[a-z0-9],?)+$', re.I)\n\ndef validate_tags(s):\n    return bool(TAGS.match(s)) and not s.endswith(',') and all(1 <= len(t) <= 20 and not t.startswith('-') and not t.endswith('-') for t in s.split(','))\n"},
     {"hidden_t.py": "import time\nfrom tags import validate_tags as v\nfor s in ('abc','a-b,c1,XYZ','ab','a1-b2-c3','x'*20,'a,b'):\n    assert v(s), s\nfor s in ('','a,,b','-a','a-','a,','x'*21,'a b','a--b,c'[0:0]+'a,-b','a_b',',a'):\n    assert not v(s), s\nassert v('a--b') == True\nfor bad in ('a'*5000+'!', ('ab-'*3000)+'!', ('a-'*4000)+'-'):\n    t=time.time(); v(bad); assert time.time()-t<0.5, len(bad)\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"tags.py": "import re\nTAG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,18}[a-z0-9])?', re.I)\n\ndef validate_tags(s):\n    if not s:\n        return False\n    return all(TAG.fullmatch(t) for t in s.split(','))\n"})

task("perf-rolling-max", "performance",
     "rolling_max(xs, k) (the maximum of every window of k consecutive values) is too slow for our 300k-point "
     "series with k=2000. Make it fast; results must be identical, including for k larger than the series (empty "
     "result) and k <= 0 (ValueError).",
     {"series.py": "def rolling_max(xs, k):\n    if k <= 0:\n        raise ValueError('k must be positive')\n    return [max(xs[i:i + k]) for i in range(len(xs) - k + 1)]\n"},
     {"hidden_t.py": "import random, time\nfrom series import rolling_max as rm\nrnd=random.Random(4)\nfor _ in range(50):\n    xs=[rnd.randint(-9,9) for _ in range(rnd.randint(0,30))]; k=rnd.randint(1,35)\n    assert rm(xs,k)==[max(xs[i:i+k]) for i in range(len(xs)-k+1)], (xs,k)\ntry:\n    rm([1],0); raise SystemExit(1)\nexcept ValueError: pass\nxs=[rnd.random() for _ in range(300000)]\nt=time.time(); r=rm(xs,2000); dt=time.time()-t\nassert len(r)==298001 and dt<3, dt\nassert r[0]==max(xs[:2000]) and r[-1]==max(xs[-2000:])\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"series.py": "from collections import deque\n\ndef rolling_max(xs, k):\n    if k <= 0:\n        raise ValueError('k must be positive')\n    q, out = deque(), []\n    for i, x in enumerate(xs):\n        while q and xs[q[-1]] <= x:\n            q.pop()\n        q.append(i)\n        if q[0] <= i - k:\n            q.popleft()\n        if i >= k - 1:\n            out.append(xs[q[0]])\n    return out\n"}, timeout=240)

task("perf-dag-paths", "performance",
     "count_paths(graph, src, dst) counts distinct paths in a directed acyclic graph modulo 1_000_000_007. It is "
     "exponential and never finishes on our build graph (thousands of nodes). Make it fast with identical results; "
     "it must not hit Python's recursion limit on long chains.",
     {"paths.py": "MOD = 1_000_000_007\n\ndef count_paths(graph, src, dst):\n    if src == dst:\n        return 1\n    return sum(count_paths(graph, n, dst) for n in graph.get(src, [])) % MOD\n"},
     {"hidden_t.py": "import time\nfrom paths import count_paths as c\nassert c({'a':['b','c'],'b':['d'],'c':['d'],'d':[]},'a','d')==2 and c({},'a','a')==1 and c({'a':['b']},'b','a')==0\ng={i:[i+1,i+2] for i in range(3000)}\nt=time.time(); r=c(g,0,3000); assert time.time()-t<3\nf=[1,1]\nfor _ in range(3000): f.append((f[-1]+f[-2])%1_000_000_007)\nassert r==f[3000], r\nchain={i:[i+1] for i in range(20000)}\nassert c(chain,0,20000)==1\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"paths.py": "MOD = 1_000_000_007\n\ndef count_paths(graph, src, dst):\n    order, seen, stack = [], set(), [(src, False)]\n    while stack:\n        n, done = stack.pop()\n        if done:\n            order.append(n); continue\n        if n in seen:\n            continue\n        seen.add(n); stack.append((n, True))\n        for m in graph.get(n, []):\n            if m not in seen:\n                stack.append((m, False))\n    ways = {}\n    for n in order:\n        ways[n] = 1 if n == dst else sum(ways.get(m, 0) for m in graph.get(n, [])) % MOD\n    return ways.get(src, 0)\n"})

task("perf-config-reload", "performance",
     "settings.get(key) is called on every request and re-reads and parses config.json each time, which dominates "
     "our CPU profile. Make it fast, but changes to config.json must still be picked up on the next call (we edit it "
     "while the service runs). Do not add background threads.",
     {"config.json": "{\"limit\": 10, \"mode\": \"fast\"}\n",
      "settings.py": "import json, os\nPATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')\nREADS = {'n': 0}\n\ndef _load():\n    READS['n'] += 1\n    with open(PATH) as f:\n        return json.load(f)\n\ndef get(key, default=None):\n    return _load().get(key, default)\n"},
     {"hidden_t.py": "import json, os, time, settings\nassert settings.get('limit')==10 and settings.get('nope',3)==3\nsettings.READS['n']=0\nt=time.time()\nfor _ in range(20000): settings.get('mode')\nassert settings.READS['n']<=2 and time.time()-t<1, settings.READS['n']\nfor v in (11, 12345):\n    time.sleep(0.01)\n    json.dump({'limit': v, 'mode':'slow'}, open(settings.PATH,'w'))\n    assert settings.get('limit')==v and settings.get('mode')=='slow', settings.get('limit')\nst=os.stat(settings.PATH)\njson.dump({'limit': 99, 'mode':'slow'}, open(settings.PATH,'w')); os.utime(settings.PATH, ns=(st.st_atime_ns, st.st_mtime_ns))\nassert settings.get('limit')==99\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"settings.py": "import json, os\nPATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')\nREADS = {'n': 0}\n_cache = {'sig': None, 'data': None}\n\ndef _load():\n    READS['n'] += 1\n    with open(PATH) as f:\n        return json.load(f)\n\ndef get(key, default=None):\n    st = os.stat(PATH)\n    sig = (st.st_mtime_ns, st.st_size, st.st_ino)\n    if sig != _cache['sig']:\n        _cache['data'], _cache['sig'] = _load(), sig\n    return _cache['data'].get(key, default)\n"})

# ---------- ambiguous requirements: the right reading is in the project itself ---------------------------------------
task("amb-accounting-export", "ambiguous",
     "Accounting asked for an export of our invoices. Add export_accounting(invoices) to exports.py returning the "
     "file contents as a string.",
     {"docs/CONVENTIONS.md": "# Data conventions\n\nEvery file we hand to accounting follows these rules:\n\n- semicolon-separated, header row first, column names in snake_case\n- dates in ISO 8601 (YYYY-MM-DD)\n- money as integer cents, never decimals\n- one row per invoice, ordered by invoice date, oldest first\n- lines end with \\n\n",
      "invoices.py": "from datetime import date\nINVOICES = [\n    {'number': 'INV-2', 'customer': 'Acme; Ltd', 'issued': date(2026, 3, 2), 'total': 1250.5},\n    {'number': 'INV-1', 'customer': 'Bo', 'issued': date(2026, 2, 27), 'total': 99.99},\n]\n",
      "exports.py": "import csv, io\n\ndef export_customers(customers):\n    buf = io.StringIO()\n    w = csv.writer(buf, delimiter=';', lineterminator='\\n')\n    w.writerow(['customer_name', 'created_on'])\n    for c in customers:\n        w.writerow([c['name'], c['created'].isoformat()])\n    return buf.getvalue()\n"},
     {"hidden_t.py": "import csv, io\nfrom invoices import INVOICES\nfrom exports import export_accounting\nout=export_accounting(INVOICES)\nrows=list(csv.reader(io.StringIO(out), delimiter=';'))\nhdr=rows[0]\nassert all(h==h.lower() and ' ' not in h for h in hdr), hdr\nassert len(rows)==3 and '\\r' not in out\nbody=rows[1:]\nflat=[c for r in body for c in r]\nassert '2026-02-27' in body[0] and '2026-03-02' in body[1], body\nassert '9999' in body[0] and '125050' in body[1], body\nassert not any('.' in c and c.replace('.','').isdigit() for c in flat), 'decimal money'\nassert 'Acme; Ltd' in body[1], body\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"exports.py": "import csv, io\n\ndef export_customers(customers):\n    buf = io.StringIO()\n    w = csv.writer(buf, delimiter=';', lineterminator='\\n')\n    w.writerow(['customer_name', 'created_on'])\n    for c in customers:\n        w.writerow([c['name'], c['created'].isoformat()])\n    return buf.getvalue()\n\ndef export_accounting(invoices):\n    buf = io.StringIO()\n    w = csv.writer(buf, delimiter=';', lineterminator='\\n')\n    w.writerow(['invoice_number', 'customer_name', 'issued_on', 'total_cents'])\n    for inv in sorted(invoices, key=lambda i: i['issued']):\n        w.writerow([inv['number'], inv['customer'], inv['issued'].isoformat(), int(round(inv['total'] * 100))])\n    return buf.getvalue()\n"},
     split="dev")

task("amb-search-normalize", "ambiguous",
     "Users complain product search misses things they can see in the catalogue. Make search match what users type "
     "the way the rest of the app compares names.",
     {"textutil.py": "import unicodedata\n\ndef normalize(s):\n    s = unicodedata.normalize('NFKD', s)\n    s = ''.join(ch for ch in s if not unicodedata.combining(ch))\n    return ' '.join(s.casefold().split())\n",
      "customers.py": "from textutil import normalize\n\ndef find_customer(customers, query):\n    q = normalize(query)\n    return [c for c in customers if q in normalize(c)]\n",
      "search.py": "PRODUCTS = ['Crème Brûlée Kit', 'STRASSE Map', 'Café  Noir', 'Jalapeño Salt', 'Plain Tea']\n\ndef search(query, products=PRODUCTS):\n    return [p for p in products if query in p]\n"},
     {"hidden_t.py": "from search import search\nassert search('creme brulee')==['Crème Brûlée Kit']\nassert search('cafe noir')==['Café  Noir'] and search('CAFÉ')==['Café  Noir']\nassert search('jalapeno')==['Jalapeño Salt'] and search('  plain   tea ')==['Plain Tea']\nassert search('strasse')==['STRASSE Map'] and search('Straße')==['STRASSE Map']\nassert search('zzz')==[]\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"search.py": "from textutil import normalize\n\nPRODUCTS = ['Crème Brûlée Kit', 'STRASSE Map', 'Café  Noir', 'Jalapeño Salt', 'Plain Tea']\n\ndef search(query, products=PRODUCTS):\n    q = normalize(query)\n    return [p for p in products if q in normalize(p)]\n"})

task("amb-display-names", "ambiguous",
     "The members list shows people's names differently from their profile page. Fix the members list.",
     {"profile.py": "def display_name(user):\n    if user.get('nickname'):\n        return user['nickname']\n    last = user.get('last', '')\n    return f\"{user['first']} {last[:1]}.\" if last else user['first']\n\ndef render_profile(user):\n    return f\"<h1>{display_name(user)}</h1>\"\n",
      "members.py": "def member_list(users):\n    return [f\"{u['first']} {u.get('last', '')}\".strip() for u in sorted(users, key=lambda u: u['first'])]\n"},
     {"hidden_t.py": "from members import member_list\nfrom profile import display_name\nus=[{'first':'Zoe','last':'Adams'},{'first':'Ann','last':'Lee','nickname':'Annie'},{'first':'Bo'}]\nr=member_list(us)\nassert sorted(r)==sorted(display_name(u) for u in us), r\nassert set(r)=={'Zoe A.','Annie','Bo'}, r\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"members.py": "from profile import display_name\n\ndef member_list(users):\n    return [display_name(u) for u in sorted(users, key=lambda u: u['first'])]\n"})

task("amb-orders-pagination", "ambiguous",
     "Add pagination to the orders listing like our other list endpoints.",
     {"products.py": "from paging import paginate\nPRODUCTS = [{'id': i} for i in range(1, 46)]\n\ndef list_products(query):\n    return paginate(PRODUCTS, query)\n",
      "paging.py": "import math\nDEFAULT, MAX = 20, 100\n\ndef paginate(items, query):\n    try:\n        page = int(query.get('page', 1)); per = int(query.get('per_page', DEFAULT))\n    except (TypeError, ValueError):\n        return 400, {'error': 'page and per_page must be integers'}\n    if page < 1 or per < 1:\n        return 400, {'error': 'page and per_page must be positive'}\n    per = min(per, MAX)\n    start = (page - 1) * per\n    return 200, {'items': items[start:start + per], 'page': page, 'per_page': per, 'total': len(items),\n                 'pages': math.ceil(len(items) / per)}\n",
      "orders.py": "ORDERS = [{'id': i, 'total': i * 2} for i in range(1, 131)]\n\ndef list_orders(query):\n    return 200, ORDERS\n"},
     {"hidden_t.py": "from orders import list_orders, ORDERS\ns,b=list_orders({})\nassert s==200 and b['page']==1 and b['per_page']==20 and b['total']==130 and b['pages']==7 and b['items']==ORDERS[:20], b.keys() if isinstance(b,dict) else b\ns,b=list_orders({'page':'7','per_page':'20'}); assert b['items']==ORDERS[120:]\ns,b=list_orders({'per_page':'500'}); assert b['per_page']==100 and len(b['items'])==100 and b['pages']==2\nassert list_orders({'page':'0'})[0]==400 and list_orders({'page':'x'})[0]==400 and 'error' in list_orders({'page':'x'})[1]\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"orders.py": "from paging import paginate\nORDERS = [{'id': i, 'total': i * 2} for i in range(1, 131)]\n\ndef list_orders(query):\n    return paginate(ORDERS, query)\n"})

task("amb-signup-errors", "ambiguous",
     "Make the signup endpoint's errors consistent with the rest of the API.",
     {"errors.py": "def validation_error(fields):\n    \"\"\"The API's one shape for invalid input: 422 and every problem by field.\"\"\"\n    return 422, {'error': 'validation_failed', 'fields': {k: list(v) for k, v in fields.items()}}\n",
      "products.py": "from errors import validation_error\n\ndef create_product(body):\n    fields = {}\n    if not body.get('name'):\n        fields.setdefault('name', []).append('is required')\n    if not isinstance(body.get('price'), (int, float)) or body.get('price', 0) <= 0:\n        fields.setdefault('price', []).append('must be a positive number')\n    if fields:\n        return validation_error(fields)\n    return 201, {'id': 1}\n",
      "signup.py": "def signup(body):\n    if not body.get('email') or '@' not in body.get('email', ''):\n        return 400, 'Invalid email'\n    if len(body.get('password', '')) < 8:\n        return 400, 'Password too short'\n    return 201, {'id': 7}\n"},
     {"hidden_t.py": "from signup import signup\nassert signup({'email':'a@b.co','password':'x'*8})==(201,{'id':7})\ns,b=signup({'email':'nope','password':'short'})\nassert s==422 and b['error']=='validation_failed' and set(b['fields'])=={'email','password'} and all(isinstance(v,list) and v for v in b['fields'].values()), (s,b)\ns,b=signup({'password':'longenough'}); assert s==422 and list(b['fields'])==['email']\ns,b=signup({'email':'a@b.co'}); assert s==422 and list(b['fields'])==['password']\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"signup.py": "from errors import validation_error\n\ndef signup(body):\n    fields = {}\n    if not body.get('email') or '@' not in body.get('email', ''):\n        fields.setdefault('email', []).append('must be a valid email address')\n    if len(body.get('password', '')) < 8:\n        fields.setdefault('password', []).append('must be at least 8 characters')\n    if fields:\n        return validation_error(fields)\n    return 201, {'id': 7}\n"})

task("amb-invoice-cents", "ambiguous",
     "Finance says our invoice totals are sometimes a cent off from their spreadsheet. Fix it.",
     {"FINANCE.md": "# How finance computes an invoice\n\n1. Each line: quantity x unit price, rounded to the cent, halves rounded up (0.005 -> 0.01).\n2. Subtotal: the sum of the rounded lines.\n3. Tax: 7.5% of the subtotal, rounded to the cent, halves rounded up.\n4. Total: subtotal + tax.\n",
      "invoice.py": "def totals(lines, tax_rate=0.075):\n    subtotal = sum(q * p for q, p in lines)\n    tax = subtotal * tax_rate\n    return {'subtotal': round(subtotal, 2), 'tax': round(tax, 2), 'total': round(subtotal + tax, 2)}\n"},
     {"hidden_t.py": "from invoice import totals\nassert totals([(3,0.335)])=={'subtotal':1.01,'tax':0.08,'total':1.09}, totals([(3,0.335)])\nassert totals([(1,0.005),(1,0.005)])=={'subtotal':0.02,'tax':0.0,'total':0.02}, totals([(1,0.005),(1,0.005)])\nassert totals([(2,10.0),(1,2.5)])=={'subtotal':22.5,'tax':1.69,'total':24.19}\nassert totals([(7,1.115)])=={'subtotal':7.81,'tax':0.59,'total':8.4}, totals([(7,1.115)])\nassert totals([])=={'subtotal':0.0,'tax':0.0,'total':0.0}\nprint('ok')\n"},
     "python3 hidden_t.py",
     {"invoice.py": "from decimal import Decimal, ROUND_HALF_UP\n\ndef _c(x):\n    return Decimal(x).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n\ndef totals(lines, tax_rate=0.075):\n    subtotal = sum((_c(Decimal(str(q)) * Decimal(str(p))) for q, p in lines), Decimal('0'))\n    tax = _c(subtotal * Decimal(str(tax_rate)))\n    return {'subtotal': float(subtotal), 'tax': float(tax), 'total': float(subtotal + tax)}\n"})
