"""The hard suite: realistic multi-file tasks, checked by code the agent never sees.

Each task has seeded files, a prompt, a `check(root, final_text) -> (passed, note)` and (for our own tests only) a reference `solution`:
the tests prove every check FAILS on the untouched starting files and PASSES on the reference solution, so a score means something."""
import hashlib
import json
import random
import re
import sys
from pathlib import Path

from .evals import _run

T = 600   # seconds per task


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _py(root: Path, code: str, timeout: int = 60):
    import subprocess
    p = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def _tests_untouched(root: Path, name: str, original: str) -> bool:
    f = root / name
    return f.exists() and _sha(f.read_text()) == _sha(original)


# ------------------------------------------------------------------ 1. pagination bugs across two files
PAGINATE = '''def page_count(total, per_page):
    return total // per_page


def get_page(items, page, per_page):
    start = page * per_page
    return items[start:start + per_page]


def page_range(page, pages, window=2):
    lo = max(1, page - window)
    hi = min(pages, page + window)
    return list(range(lo, hi))
'''
PAGINATE_FIXED = PAGINATE.replace("return total // per_page", "return -(-total // per_page)").replace("start = page * per_page", "start = (page - 1) * per_page").replace("range(lo, hi)", "range(lo, hi + 1)")
VIEWS = '''from paginate import get_page, page_count, page_range


def render(items, page, per_page=10):
    pages = page_count(len(items), per_page)
    return {"items": get_page(items, page, per_page), "pages": pages, "nav": page_range(page, pages)}
'''
TEST_PAGINATE = '''import unittest
from paginate import page_count, get_page, page_range
from views import render


class T(unittest.TestCase):
    def test_count(self):
        self.assertEqual(page_count(25, 10), 3)
        self.assertEqual(page_count(20, 10), 2)
        self.assertEqual(page_count(0, 10), 0)

    def test_page(self):
        items = list(range(1, 26))
        self.assertEqual(get_page(items, 1, 10), list(range(1, 11)))
        self.assertEqual(get_page(items, 3, 10), [21, 22, 23, 24, 25])

    def test_range(self):
        self.assertEqual(page_range(5, 10), [3, 4, 5, 6, 7])
        self.assertEqual(page_range(1, 3), [1, 2, 3])

    def test_render(self):
        r = render(list(range(1, 26)), 2)
        self.assertEqual((r["pages"], r["items"][0], r["nav"]), (3, 11, [1, 2, 3]))


unittest.main()
'''


def check_paginate(root, final):
    ok_t = _tests_untouched(root, "test_paginate.py", TEST_PAGINATE)
    rc, out = _run(root, sys.executable, "test_paginate.py")
    return rc == 0 and ok_t, ("the test file was modified" if not ok_t else out[-200:])


# ------------------------------------------------------------------ 2. implement an LRU cache from a spec
LRU_SPEC = """# LRUCache spec

`lru.py` must define `class LRUCache` with:

- `LRUCache(capacity)`: capacity must be an int >= 1, otherwise raise `ValueError`.
- `get(key, default=None)`: return the value and mark the key as most recently used; `default` if missing.
- `put(key, value)`: insert or update (an update also marks it most recently used). When the cache would exceed its capacity, evict the least recently used key.
- `len(cache)`, `key in cache` (does NOT count as a use), `cache.keys()` returning keys from least to most recently used.
- `get` and `put` must be O(1): a test does 300,000 operations on a cache of capacity 50,000.
"""
LRU_STUB = "class LRUCache:\n    raise NotImplementedError\n"
LRU_SOL = '''from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity):
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError("capacity must be an int >= 1")
        self.capacity, self._d = capacity, OrderedDict()

    def get(self, key, default=None):
        if key not in self._d:
            return default
        self._d.move_to_end(key)
        return self._d[key]

    def put(self, key, value):
        self._d[key] = value
        self._d.move_to_end(key)
        if len(self._d) > self.capacity:
            self._d.popitem(last=False)

    def __len__(self):
        return len(self._d)

    def __contains__(self, key):
        return key in self._d

    def keys(self):
        return list(self._d.keys())
'''


def check_lru(root, final):
    code = '''
import time
from lru import LRUCache
for bad in (0, -1, 1.5, "3", None):
    try:
        LRUCache(bad); raise SystemExit("accepted bad capacity %r" % (bad,))
    except ValueError: pass
c = LRUCache(2)
c.put("a", 1); c.put("b", 2); assert c.get("a") == 1
c.put("c", 3)
assert "b" not in c and "a" in c and "c" in c and len(c) == 2, c.keys()
assert c.get("zzz") is None and c.get("zzz", 7) == 7
c.put("a", 10); c.put("d", 4)
assert c.keys() == ["a", "d"], c.keys()
c2 = LRUCache(2); c2.put(1, 1); c2.put(2, 2)
assert 1 in c2 and 1 in c2            # membership is not a use
c2.put(3, 3)
assert 1 not in c2 and c2.keys() == [2, 3], c2.keys()
c3 = LRUCache(1); c3.put("x", 1); c3.put("y", 2); assert c3.keys() == ["y"]
t = time.time(); big = LRUCache(50000)
for i in range(300000):
    big.put(i, i)
    if i % 3 == 0: big.get(i - 10)
assert len(big) == 50000
assert time.time() - t < 4, "too slow: %.1fs" % (time.time() - t)
print("ok")
'''
    rc, out = _py(root, code, 90)
    return rc == 0 and "ok" in out, out[-200:]


# ------------------------------------------------------------------ 3. rename across files, with a decoy that must not change
PRICING = '''"""Pricing helpers. calc_total is the single place line items become a price."""
TAX = 0.2


def calc_total(items):
    """Sum of qty * price. See also calc_total_tax."""
    return round(sum(q * p for q, p in items), 2)


def calc_total_tax(items):
    return round(calc_total(items) * (1 + TAX), 2)
'''
RENAME_FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": PRICING,
    "shop/cart.py": "from .pricing import calc_total\n\n\nclass Cart:\n    def __init__(self):\n        self.items = []\n\n    def add(self, qty, price):\n        self.items.append((qty, price))\n\n    def total(self):\n        return calc_total(self.items)\n",
    "shop/checkout.py": "from . import pricing\n\n\ndef charge(items):\n    # uses pricing.calc_total so the receipt matches\n    amount = pricing.calc_total(items)\n    return {\"amount\": amount, \"with_tax\": pricing.calc_total_tax(items)}\n",
    "shop/reports.py": "from .pricing import calc_total as total_of\n\n\ndef summary(orders):\n    return [total_of(o) for o in orders]\n",
    "shop/cli.py": "import sys\nfrom .pricing import calc_total\n\n\ndef main(argv):\n    pairs = [tuple(map(float, a.split('x'))) for a in argv]\n    print(calc_total(pairs))\n\n\nif __name__ == '__main__':\n    main(sys.argv[1:])\n",
    "README.md": "# shop\n\n`calc_total(items)` adds up qty * price. `calc_total_tax` adds VAT on top.\n",
    "tests/test_shop.py": "import sys, os, unittest\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\nfrom shop.pricing import calc_total, calc_total_tax\nfrom shop.cart import Cart\n\n\nclass T(unittest.TestCase):\n    def test_total(self):\n        self.assertEqual(calc_total([(2, 1.5), (1, 0.25)]), 3.25)\n\n    def test_tax(self):\n        self.assertEqual(calc_total_tax([(1, 10)]), 12.0)\n\n    def test_cart(self):\n        c = Cart(); c.add(3, 2); self.assertEqual(c.total(), 6)\n\n\nunittest.main()\n",
}


def check_rename(root, final):
    left = []
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.suffix in (".py", ".md", ".txt") and re.search(r"\bcalc_total\b", p.read_text(errors="ignore")):
            left.append(str(p.relative_to(root)))
    if left:
        return False, "old name still in: " + ", ".join(left[:5])
    rc, out = _py(root, "from shop.pricing import compute_total, calc_total_tax\nfrom shop.cart import Cart\nfrom shop.checkout import charge\nfrom shop.reports import summary\n"
                        "assert compute_total([(2, 1.5)]) == 3.0 and calc_total_tax([(1, 10)]) == 12.0\n"
                        "c = Cart(); c.add(2, 5); assert c.total() == 10\nassert charge([(1, 10)]) == {'amount': 10.0, 'with_tax': 12.0}\nassert summary([[(1, 2)]]) == [2.0]\nprint('ok')")
    if rc or "ok" not in out:
        return False, out[-200:]
    t = [str(p) for p in (root / "tests").glob("test*.py")]
    rc2, out2 = _run(root, sys.executable, *t) if t else (1, "tests are gone")
    return rc2 == 0, ("tests fail: " + out2[-160:]) if rc2 else "renamed everywhere, decoy left alone, tests pass"


RENAME_SOL = {k: re.sub(r"\bcalc_total\b", "compute_total", v) for k, v in RENAME_FILES.items()}


# ------------------------------------------------------------------ 4. CLI feature with hidden output tests
WORDFREQ = '''import sys
from collections import Counter


def main(argv):
    text = open(argv[0]).read() if argv else sys.stdin.read()
    words = [w.strip(".,;:!?\\"'()").lower() for w in text.split()]
    counts = Counter(w for w in words if w)
    for w, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{n} {w}")


if __name__ == "__main__":
    main(sys.argv[1:])
'''
WORDFREQ_SOL = '''import argparse
import sys
from collections import Counter


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?")
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument("--min-length", type=int, default=1)
    ap.add_argument("--case-sensitive", action="store_true")
    a = ap.parse_args(argv)
    text = open(a.file).read() if a.file else sys.stdin.read()
    words = [w.strip(".,;:!?\\"'()") for w in text.split()]
    if not a.case_sensitive:
        words = [w.lower() for w in words]
    counts = Counter(w for w in words if w and len(w) >= a.min_length)
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if a.top is not None:
        rows = rows[:a.top]
    for w, n in rows:
        print(f"{n} {w}")


if __name__ == "__main__":
    main(sys.argv[1:])
'''
WF_TEXT = "The cat and the dog. The Cat sat; the dog ran! A cat? And a bird.\nBird bird cat\n"


def check_wordfreq(root, final):
    import subprocess
    (root / "_in.txt").write_text(WF_TEXT)

    def run(*args, stdin=None):
        p = subprocess.run([sys.executable, "wordfreq.py", *args], cwd=root, capture_output=True, text=True, input=stdin, timeout=30)
        return p.returncode, p.stdout
    rc, base = run("_in.txt")
    if rc or not base.startswith("4 cat\n4 the\n3 bird\n"):
        return False, "default behaviour changed or broken: " + base[:80]
    for args, want in ((("--top", "2"), "4 cat\n4 the\n"), (("--min-length", "4"), "3 bird\n"), (("--min-length", "3", "--top", "3"), "4 cat\n4 the\n3 bird\n")):
        rc, got = run("_in.txt", *args)
        if got != want:
            return False, f"{' '.join(args)} gave {got!r}"
    rc, cs = run("_in.txt", "--case-sensitive")
    if "1 Cat" not in cs or "2 The" not in cs:
        return False, "--case-sensitive should keep 'Cat' and 'The' separate: " + cs[:100]
    rc, std = run("--top", "1", stdin=WF_TEXT)
    if std != "4 cat\n":
        return False, f"stdin with --top gave {std!r}"
    return True, "flags work, default output unchanged"


# ------------------------------------------------------------------ 6. scheduler with three classic Python bugs
SCHED = '''class Scheduler:
    def __init__(self, name, jobs=[]):
        self.name = name
        self.jobs = jobs

    def add(self, label, minutes):
        self.jobs.append((label, minutes))

    def average(self):
        return sum(m for _, m in self.jobs) // len(self.jobs)

    def callbacks(self):
        fns = []
        for label, minutes in self.jobs:
            fns.append(lambda: f"{label}:{minutes}")
        return fns


def busiest(schedulers):
    return max(schedulers, key=lambda s: sum(m for _, m in s.jobs)).name
'''
SCHED_SOL = SCHED.replace("jobs=[]):\n        self.name = name\n        self.jobs = jobs", "jobs=None):\n        self.name = name\n        self.jobs = list(jobs) if jobs else []").replace("// len(self.jobs)", "/ len(self.jobs)").replace("lambda: f\"{label}:{minutes}\"", "lambda label=label, minutes=minutes: f\"{label}:{minutes}\"")
TEST_SCHED = '''import unittest
from scheduler import Scheduler, busiest


class T(unittest.TestCase):
    def test_instances_are_independent(self):
        a, b = Scheduler("a"), Scheduler("b")
        a.add("x", 10)
        self.assertEqual(b.jobs, [])

    def test_average_is_exact(self):
        s = Scheduler("s"); s.add("a", 5); s.add("b", 10)
        self.assertEqual(s.average(), 7.5)

    def test_callbacks_remember_their_own_job(self):
        s = Scheduler("s"); s.add("a", 1); s.add("b", 2); s.add("c", 3)
        self.assertEqual([f() for f in s.callbacks()], ["a:1", "b:2", "c:3"])

    def test_busiest(self):
        a, b = Scheduler("a"), Scheduler("b")
        a.add("x", 5); b.add("y", 9)
        self.assertEqual(busiest([a, b]), "b")


unittest.main()
'''


def check_sched(root, final):
    ok_t = _tests_untouched(root, "test_scheduler.py", TEST_SCHED)
    rc, out = _run(root, sys.executable, "test_scheduler.py")
    return rc == 0 and ok_t, ("the test file was modified" if not ok_t else out[-200:])


# ------------------------------------------------------------------ 7. needle in a generated codebase
def _needle_files():
    rnd = random.Random(7)
    files = {}
    nouns = ["invoice", "ticket", "order", "account", "coupon", "license", "payment", "shipment", "session", "profile"]
    verbs = ["refund", "cancel", "renew", "audit", "export", "archive", "notify", "reconcile"]
    for i in range(38):
        n, v = rnd.choice(nouns), rnd.choice(verbs)
        files[f"svc/mod_{i:02d}.py"] = (f"class {n.title()}Error(Exception):\n    pass\n\n\n"
                                         f"def {v}_{n}(rec, days_since=0):\n    if rec.get('state') == 'closed':\n        raise {n.title()}Error('closed')\n"
                                         f"    return {{'{v}': rec.get('id'), 'days': days_since}}\n")
    files["svc/mod_07.py"] = ("class RefundWindowClosed(Exception):\n    pass\n\n\ndef refund_expired_subscription(sub, days_since):\n    if sub['trial_expired'] and days_since > 14:\n"
                              "        raise RefundWindowClosed('too late')\n    return {'refund': sub['id']}\n")
    files["svc/mod_19.py"] = ("class GracePeriodExceeded(Exception):\n    pass\n\n\ndef handle_expired_refund(sub, days_since):\n    \"\"\"Refund a subscription whose trial has expired, unless 30 days have passed.\"\"\"\n"
                              "    if not sub['trial_expired']:\n        raise ValueError('trial still running')\n    if days_since > 30:\n        raise GracePeriodExceeded('grace period over')\n    return {'refund': sub['id']}\n")
    files["svc/mod_31.py"] = ("class LegacyRefundError(Exception):\n    pass\n\n\ndef refund_legacy_plan(sub, days_since):\n    if days_since > 30:\n        raise LegacyRefundError('old plans are final')\n    return {'refund': sub['id']}\n")
    files["README.md"] = "# svc\nOne module per feature.\n"
    return files


def check_needle(root, final):
    m = re.search(r"FILE=(\S+)\s+FUNC=(\S+)\s+EXC=(\S+)", final or "")
    if not m:
        return False, "no final line like FILE=… FUNC=… EXC=…"
    f, fn, exc = (x.strip("`.,") for x in m.groups())
    ok = f.endswith("svc/mod_19.py") and fn.startswith("handle_expired_refund") and exc == "GracePeriodExceeded"
    return ok, f"answered {f} {fn} {exc}"


# ------------------------------------------------------------------ 8. circular import
CYCLE_FILES = {
    "app/__init__.py": "",
    "app/config.py": "FEE = 3\n",
    "app/a.py": "from .b import helper_b\nfrom .config import FEE\n\nCONST = 10\n\n\ndef run(x):\n    return helper_b(x) + FEE\n",
    "app/b.py": "from .c import compute\n\n\ndef helper_b(x):\n    return compute(x) * 2\n",
    "app/c.py": "from .a import CONST\n\n\ndef compute(x):\n    return x + CONST\n",
}
CYCLE_TEST = '''import unittest, importlib


class T(unittest.TestCase):
    def test_import_orders(self):
        for first in ("app.a", "app.b", "app.c"):
            import subprocess, sys
            r = subprocess.run([sys.executable, "-c", f"import {first}; import app.a, app.b, app.c; print(app.a.run(1))"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            self.assertEqual(r.stdout.strip(), "25")

    def test_public_api(self):
        a = importlib.import_module("app.a"); c = importlib.import_module("app.c")
        self.assertEqual(a.run(2), 27)
        self.assertEqual(c.compute(5), 15)
        self.assertEqual(a.CONST, 10)


unittest.main()
'''
CYCLE_SOL = dict(CYCLE_FILES, **{"app/c.py": "from .config import CONST\n\n\ndef compute(x):\n    return x + CONST\n", "app/config.py": "FEE = 3\nCONST = 10\n", "app/a.py": "from .b import helper_b\nfrom .config import FEE, CONST\n\n\ndef run(x):\n    return helper_b(x) + FEE\n"})


def check_cycle(root, final):
    ok_t = _tests_untouched(root, "test_cycle.py", CYCLE_TEST)
    rc, out = _run(root, sys.executable, "test_cycle.py")
    return rc == 0 and ok_t, ("the test file was modified" if not ok_t else out[-250:])


# ------------------------------------------------------------------ 9. C++ sieve, compiled
def check_sieve(root, final):
    import subprocess, time
    exe = root / "primes"
    if not (root / "primes.cpp").exists():
        return False, "primes.cpp is missing"
    if not exe.exists():
        return False, "no compiled ./primes binary"
    for n, want in ((1, 0), (2, 1), (10, 4), (100, 25), (10_000_000, 664579), (50_000_000, 3001134)):
        t = time.time()
        try:
            p = subprocess.run([str(exe)], input=f"{n}\n", capture_output=True, text=True, timeout=20, cwd=root)
        except subprocess.TimeoutExpired:
            return False, f"N={n} took over 20s"
        if p.stdout.strip() != str(want):
            return False, f"N={n}: expected {want}, got {p.stdout.strip()!r}"
        if n == 50_000_000 and time.time() - t > 6:
            return False, f"N=5e7 took {time.time() - t:.1f}s"
    return True, "correct up to 5e7 and fast"


SIEVE_SOL = {"primes.cpp": '#include <cstdio>\n#include <vector>\nint main(){long n;if(scanf("%ld",&n)!=1)return 1;if(n<2){puts("0");return 0;}std::vector<bool> c(n+1,false);long k=0;for(long i=2;i<=n;i++){if(!c[i]){k++;for(long j=i*i;j<=n;j+=i)c[j]=true;}}printf("%ld\\n",k);}\n'}


# ------------------------------------------------------------------ 10. SQL aggregate report
def _orders_csv():
    rnd = random.Random(11)
    rows = ["id,customer,qty,price,status"]
    for i in range(1, 241):
        rows.append(f"{i},{rnd.choice(['ann','bob','cy','dee','eve','fay','gus'])},{rnd.randint(1, 6)},{rnd.choice([4.5, 9.99, 15, 22.25, 3.1])},{rnd.choice(['paid', 'paid', 'paid', 'refunded', 'pending'])}")
    return "\n".join(rows) + "\n"


ORDERS = _orders_csv()


def _expected_report():
    import csv, io
    from collections import defaultdict
    tot, cnt = defaultdict(float), defaultdict(int)
    for r in csv.DictReader(io.StringIO(ORDERS)):
        if r["status"] == "paid":
            tot[r["customer"]] += int(r["qty"]) * float(r["price"])
            cnt[r["customer"]] += 1
    rows = [(c, t, cnt[c]) for c, t in tot.items() if t > 100]
    rows.sort(key=lambda x: (-x[1], x[0]))
    return "customer,total,orders\n" + "".join(f"{c},{t:.2f},{n}\n" for c, t, n in rows)


def check_report(root, final):
    rc, out = _run(root, sys.executable, "report.py")
    want = _expected_report()
    return rc == 0 and out.strip() == want.strip(), ("wrong output; first lines: " + out[:120]) if out.strip() != want.strip() else "report matches"


REPORT_SOL = {"report.py": '''import csv, sqlite3
db = sqlite3.connect(":memory:")
db.execute("create table o(id int, customer text, qty int, price real, status text)")
with open("orders.csv") as f:
    db.executemany("insert into o values (?,?,?,?,?)", [(r["id"], r["customer"], int(r["qty"]), float(r["price"]), r["status"]) for r in csv.DictReader(f)])
print("customer,total,orders")
for c, t, n in db.execute("select customer, sum(qty*price) t, count(*) from o where status='paid' group by customer having t > 100 order by t desc, customer"):
    print(f"{c},{t:.2f},{n}")
'''}


# ------------------------------------------------------------------ 11. JavaScript bugs (node --test)
CART_JS = '''function addItem(cart, name, priceCents, qty = 1) {
  cart.items.push({ name, priceCents, qty });
}

function removeItem(cart, index) {
  cart.items.splice(index, 2);
}

function subtotal(cart) {
  return cart.items.reduce((s, i) => s + i.priceCents, 0) / 100;
}

function total(cart, discountPct = 0) {
  const sub = subtotal(cart);
  return sub - discountPct / 100;
}

module.exports = { addItem, removeItem, subtotal, total };
'''
CART_SOL = CART_JS.replace("s + i.priceCents, 0", "s + i.priceCents * i.qty, 0").replace("splice(index, 2)", "splice(index, 1)").replace("return sub - discountPct / 100;", "return Math.round(sub * (1 - discountPct / 100) * 100) / 100;")
CART_TEST = '''const test = require('node:test');
const assert = require('node:assert');
const { addItem, removeItem, subtotal, total } = require('./cart');

const mk = () => ({ items: [] });

test('subtotal in currency units', () => {
  const c = mk(); addItem(c, 'a', 1999, 2); addItem(c, 'b', 501);
  assert.strictEqual(subtotal(c), 44.99);
});

test('remove one item only', () => {
  const c = mk(); addItem(c, 'a', 100); addItem(c, 'b', 200); addItem(c, 'c', 300);
  removeItem(c, 1);
  assert.deepStrictEqual(c.items.map((i) => i.name), ['a', 'c']);
});

test('percentage discount applies to the subtotal and rounds to cents', () => {
  const c = mk(); addItem(c, 'a', 1999, 3);
  assert.strictEqual(total(c, 10), 53.97);
  assert.strictEqual(total(c, 0), 59.97);
  assert.strictEqual(total(c, 33), 40.18);
});
'''


def check_cart(root, final):
    ok_t = _tests_untouched(root, "cart.test.js", CART_TEST)
    rc, out = _run(root, "node", "--test", "cart.test.js")
    return rc == 0 and ok_t, ("the test file was modified" if not ok_t else out[-250:])


# ------------------------------------------------------------------ 13. security fixes
FILES_APP = '''import os
import sqlite3

UPLOADS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")


def read_upload(name):
    with open(os.path.join(UPLOADS, name)) as f:
        return f.read()


def find_user(db, username):
    row = db.execute(f"SELECT id, name FROM users WHERE name = '{username}'").fetchone()
    return row


def render_comment(text):
    return "<p class='comment'>" + text + "</p>"
'''
FILES_APP_SOL = '''import html
import os
import sqlite3

UPLOADS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")


def read_upload(name):
    path = os.path.realpath(os.path.join(UPLOADS, name))
    if os.path.commonpath([path, os.path.realpath(UPLOADS)]) != os.path.realpath(UPLOADS):
        raise ValueError("path escapes the uploads folder")
    with open(path) as f:
        return f.read()


def find_user(db, username):
    return db.execute("SELECT id, name FROM users WHERE name = ?", (username,)).fetchone()


def render_comment(text):
    return "<p class='comment'>" + html.escape(text) + "</p>"
'''


def check_security(root, final):
    code = '''
import sqlite3, os
import files_app as f
assert f.read_upload("hello.txt").strip() == "hi there"
db = sqlite3.connect(":memory:"); db.execute("create table users(id integer, name text)"); db.execute("insert into users values (1, 'ann'), (2, 'bob')")
assert f.find_user(db, "ann") == (1, "ann") and f.find_user(db, "nobody") is None
assert f.find_user(db, "' OR '1'='1") is None, "sql injection works"
assert f.find_user(db, "x' UNION SELECT 1, 'pwn' --") is None, "union injection works"
for bad in ("../secret.txt", "../../etc/passwd", "uploads/../../secret.txt", os.path.abspath("secret.txt"), "sub/../../secret.txt"):
    try:
        f.read_upload(bad); raise SystemExit("traversal worked for %r" % bad)
    except ValueError: pass
    except FileNotFoundError: raise SystemExit("must raise ValueError, not FileNotFoundError, for %r" % bad)
assert f.read_upload("sub/inner.txt").strip() == "inner", "legit subfolder broke"
out = f.render_comment("<script>alert(1)</script>")
assert "<script>" not in out and "&lt;script&gt;" in out, out
assert f.render_comment("a & b") == "<p class='comment'>a &amp; b</p>"
print("ok")
'''
    rc, out = _py(root, code)
    return rc == 0 and "ok" in out, out[-250:]


# ------------------------------------------------------------------ 14. broken Makefile / C build
MAKEFILE = "CC = gcc\nCFLAGS = -Wall -O2\nOBJ = main.o util.o\n\napp: $(OBJS)\n\t$(CC) $(CFLAGS) -o app $(OBJ)\n\n%.o: %.c\n\t$(CC) $(CFLAGS) -c $<\n\nclean:\n\trm -f app *.o\n"
MAIN_C = '#include <stdio.h>\n#include "util.h"\n\nint main(void) {\n    printf("sum=%d sqrt=%.2f\\n", sum_to(10) , root_of(50));\n    return 0;\n}\n'
UTIL_H = "int sum_to(int n);\ndouble root_of(double x);\n"
UTIL_C = '#include "util.h"\n\nint sum_to(int n) {\n    int s = 0;\n    for (int i = 1; i <= n; i++) s += i;\n    return s;\n}\n\ndouble root_of(double x) {\n    return sqrt(x);\n}\n'
MAKE_SOL = {"Makefile": MAKEFILE.replace("$(OBJS)", "$(OBJ)").replace("-o app $(OBJ)", "-o app $(OBJ) -lm"), "util.c": "#include <math.h>\n" + UTIL_C}


def check_make(root, final):
    _run(root, "make", "clean")
    rc, out = _run(root, "make")
    if rc:
        return False, "make still fails: " + out[-200:]
    rc, out = _run(root, "./app")
    return rc == 0 and out.strip() == "sum=55 sqrt=7.07", out[-120:]


# ------------------------------------------------------------------ the suite
HARD_TASKS: list = [
    {"name": "Fix pagination bugs", "timeout": T, "files": {"paginate.py": PAGINATE, "views.py": VIEWS, "test_paginate.py": TEST_PAGINATE},
     "prompt": "`python3 test_paginate.py` fails. The bugs are in the code, not the tests: fix them (do NOT edit the test file) and run the tests until they pass.",
     "check": check_paginate, "solution": {"paginate.py": PAGINATE_FIXED}},
    {"name": "Implement an LRU cache", "timeout": T, "files": {"SPEC.md": LRU_SPEC, "lru.py": LRU_STUB},
     "prompt": "Implement `lru.py` exactly as described in SPEC.md. Write your own quick tests and run them; performance requirements are part of the spec.",
     "check": check_lru, "solution": {"lru.py": LRU_SOL}},
    {"name": "Rename across the project", "timeout": T, "files": RENAME_FILES,
     "prompt": "Rename the function `calc_total` to `compute_total` everywhere in this project: definition, imports, calls, docstrings, comments, README and tests. Behaviour must not change. "
               "Careful: `calc_total_tax` is a different function and keeps its name. Run the tests afterwards.",
     "check": check_rename, "solution": RENAME_SOL},
    {"name": "Add CLI flags", "timeout": T, "files": {"wordfreq.py": WORDFREQ},
     "prompt": "wordfreq.py prints word counts from a file or stdin. Add these options without changing the default output: `--top N` (only the N most frequent), "
               "`--min-length L` (ignore words shorter than L) and `--case-sensitive` (by default words are lower-cased, with this flag they are not). Test it yourself.",
     "check": check_wordfreq, "solution": {"wordfreq.py": WORDFREQ_SOL}},
    {"name": "Three subtle Python bugs", "timeout": T, "files": {"scheduler.py": SCHED, "test_scheduler.py": TEST_SCHED},
     "prompt": "`python3 test_scheduler.py` fails. Fix scheduler.py (never the tests) and run the tests until everything passes.",
     "check": check_sched, "solution": {"scheduler.py": SCHED_SOL}},
    {"name": "Find the right function", "timeout": T, "files": _needle_files(),
     "prompt": "In this codebase, which function refunds a subscription whose trial has expired AND raises an error when more than 30 days have passed? "
               "Find it, do not guess. End your answer with a final line formatted exactly: FILE=<path> FUNC=<name> EXC=<exception class>",
     "check": check_needle, "solution": {}, "final": "It is handle_expired_refund.\nFILE=svc/mod_19.py FUNC=handle_expired_refund EXC=GracePeriodExceeded"},
    {"name": "Break an import cycle", "timeout": T, "files": dict(CYCLE_FILES, **{"test_cycle.py": CYCLE_TEST}),
     "prompt": "`python3 test_cycle.py` fails with an import error. Fix the circular import by restructuring the package, without changing any public name or behaviour, and without editing the test file.",
     "check": check_cycle, "solution": CYCLE_SOL},
    {"name": "C++ prime counter", "timeout": T, "files": {},
     "prompt": "Write primes.cpp: it reads one integer N from stdin and prints how many primes are <= N (N can be up to 50,000,000 and must finish in a few seconds). "
               "Compile it with `g++ -O2 -o primes primes.cpp` so the binary ./primes exists, and test it.",
     "check": check_sieve, "solution": SIEVE_SOL},
    {"name": "SQL report", "timeout": T, "files": {"orders.csv": ORDERS},
     "prompt": "orders.csv has columns id,customer,qty,price,status. Write report.py that loads it into an in-memory SQLite database and prints to stdout a CSV report: header `customer,total,orders`, "
               "then one line per customer whose total is above 100, where total = sum(qty*price) over their `paid` orders only (2 decimals) and orders = how many paid orders. Sort by total descending, then name. Run it.",
     "check": check_report, "solution": REPORT_SOL},
    {"name": "Fix JavaScript cart", "timeout": T, "files": {"cart.js": CART_JS, "cart.test.js": CART_TEST},
     "prompt": "`node --test cart.test.js` fails. Fix cart.js (not the tests) and run the tests until they pass.",
     "check": check_cart, "solution": {"cart.js": CART_SOL}},
    {"name": "Close security holes", "timeout": T, "files": {"files_app.py": FILES_APP, "uploads/hello.txt": "hi there\n", "uploads/sub/inner.txt": "inner\n", "secret.txt": "top secret\n"},
     "prompt": "files_app.py has three security bugs. Fix them without breaking normal use: `read_upload` must never read outside the uploads folder (raise ValueError for any escape attempt, "
               "but sub-folders inside uploads are fine), `find_user` must be immune to SQL injection, `render_comment` must not allow HTML/script injection. Write a quick test of the attacks and run it.",
     "check": check_security, "solution": {"files_app.py": FILES_APP_SOL}},
    {"name": "Repair a broken build", "timeout": T, "files": {"Makefile": MAKEFILE, "main.c": MAIN_C, "util.h": UTIL_H, "util.c": UTIL_C},
     "prompt": "`make` fails in this C project. Fix the build (do not rewrite the program) so that `make && ./app` prints exactly: sum=55 sqrt=7.07",
     "check": check_make, "solution": MAKE_SOL},
]
