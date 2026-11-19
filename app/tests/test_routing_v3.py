"""Routing regression set v3: labelled requests -> spec (+ variant). Deterministic rules only, no model call."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import hrouter as HRR  # noqa: E402
from meshpool_harness.hrouter import signals  # noqa: E402
from meshpool_harness.hrouter.route import addon  # noqa: E402

DEV = Path(__file__).resolve().parents[2] / "testkit" / "loop" / "suites" / "v2_dev.json"
DEV_LABELS = {  # id -> (spec, variant or None for "any")
    "v2-alg-intervals": ("code.build", None), "v2-data-dupes-fx": ("data", None),
    "v2-dbg-memo-kwargs": ("code.debug", None), "v2-mf-soft-delete": ("code.build", None),
    "v2-perf-anagrams": ("code.debug", "perf"), "v2-amb-accounting-export": ("code.build", None),
    "v2-rf-payment-strategies": ("code.refactor", None), "v2-te-slugify": ("code.test", None),
}

# (text, spec, variant or None)
CASES = [
    # tests
    ("Write thorough tests for slug.slugify (see its code for the intended behaviour: accents folded). Put the tests in "
     "test_slug.py as a plain Python script. Do not change slug.py.", "code.test", None),
    ("Add unit tests for the parse_date helper in utils.py covering the cases where it raises ValueError.", "code.test", None),
    ("Write pytest tests for cart.total(); it currently fails on empty carts, so include that case. Don't touch cart.py.",
     "code.test", None),
    ("Increase test coverage of the billing module, especially the refund path that returns wrong amounts sometimes.",
     "code.test", None),
    ("Generate integration tests for the /orders endpoint. They should check that a bad id returns 404.", "code.test", None),
    ("Write tests that pin down how Stack.pop behaves on an empty stack, put them in test_stack.py.", "code.test", None),
    # features
    ("Add a --json flag to the cli that prints the report as JSON instead of a table.", "code.build", "cli"),
    ("Add a GET /health endpoint that returns the app version and uptime.", "code.build", "api"),
    ("Orders must be cancellable: add cancel(order_id) that marks an order cancelled and releases its stock; all "
     "existing reads should treat cancelled orders as absent. Keep all current behaviour otherwise.", "code.build", None),
    ("The cache should support a max size: add max_items to Cache(), evicting the least recently used entry.",
     "code.build", None),
    ("Make export_csv() quote fields that contain commas.", "code.build", None),
    ("Support environment variable overrides in config.load(); precedence file < env.", "code.build", None),
    ("Implement a function chunk(items, n) that splits a list into pieces of size n; the last piece may be shorter.",
     "code.build", "function"),
    ("Add rate limiting to the api: each client may make 100 requests per minute, return 429 afterwards.",
     "code.build", "api"),
    ("Create a small script that renames every photo in a folder by its EXIF date.", "code.build", None),
    ("Users should be able to archive projects; add archive(id) and unarchive(id), and hide archived projects from "
     "list_projects and count_projects.", "code.build", None),
    ("Implement top_k(stream, k) in topk.py: ties broken by earliest arrival, must run in O(n log k), k may exceed the "
     "stream length.", "code.build", "algorithm"),
    ("Write the function next_business_day(date, holidays) in bizdays.py. Rules: weekends skipped, holidays skipped, "
     "a holiday on Friday pushes to Monday.", "code.build", "algorithm"),
    # multi-file
    ("Add audit logging: every write to accounts, invoices and users must append an entry to the audit log. "
     "Keep all current behaviour otherwise.", "code.build", "feature"),
    ("Soft-delete projects: every existing read (get, list, count, search) must behave as if deleted projects do not "
     "exist. Keep all current behaviour.", "code.build", "feature"),
    # refactor
    ("Refactor the notifier module so each channel is its own class; behaviour must not change.", "code.refactor", None),
    ("Clean up utils.py: extract the date helpers into dates.py and rename every use.", "code.refactor", None),
    ("Migrate the project from requests to httpx.", "code.refactor", None),
    # debug
    ('I get this: Traceback (most recent call last):\n  File "app.py", line 12, in <module>\n    main()\nKeyError: "id"',
     "code.debug", None),
    ("parse_price('1,234.50') returns the wrong value, it gives 1.0 instead of 1234.5. Fix it.", "code.debug", None),
    ("The login page used to work but since the last merge it crashes with a 500 on submit.", "code.debug", None),
    ("test_checkout.py::test_discount is failing with AssertionError: 90 != 95. Find the bug.", "code.debug", None),
    ("The report job is too slow: it takes 40 minutes on 1M rows. Make it fast, same output.", "code.debug", "perf"),
    ("Our import times out on large files. Speed it up.", "code.debug", "perf"),
    ("Why does main.py crash with a segfault when the list is empty? Fix it.", "code.debug", None),
    ("quote() raises ValueError for negative quantities though the spec says it should clamp to zero; fix the bug.",
     "code.debug", None),
    # review / explain / terminal
    ("Review my pull request diff for security problems.", "code.review", None),
    ("Explain how this code works: what does the scheduler class do?", "code.explain", None),
    ("Install the dependencies and set up docker compose for local dev.", "code.terminal", None),
    # non-code
    ("Analyze sales.csv and tell me which region declined fastest.", "data", None),
    ("What share of orders in orders.csv were refunded? Give the percent to one decimal.", "data", None),
    ("Make a 10 slide deck on our Q3 results for the board.", "docs", None),
    ("Research the competitive landscape for vector databases with sources.", "research", None),
    ("what's the capital of Australia?", "chat", None),
    ("rewrite this email to sound friendlier: hey, send the files asap", "chat", None),
    ("Every Monday morning summarise my unread email and remind me of deadlines.", "automation", None),
]


def _score(rows, kind_of, workdir):
    bad = []
    for name, text, spec, var in rows:
        r = HRR.route(text, kind=kind_of(spec), workdir=workdir)
        if r.spec != spec or (var is not None and r.variant != var):
            bad.append((name, spec, var, r.spec, r.variant))
    return bad


def _kind(spec):
    return "cowork" if spec in ("data", "docs", "research", "chat", "automation") else "code"


def _rows():
    rows = [(t[:50], t, s, v) for t, s, v in CASES]
    if DEV.exists():
        for t in json.loads(DEV.read_text(encoding="utf-8")):
            if t["id"] in DEV_LABELS:
                s, v = DEV_LABELS[t["id"]]
                rows.append((t["id"], t["prompt"], s, v))
    return rows


@pytest.fixture(params=["none", "project"])
def workdir(request, tmp_path):
    if request.param == "none":
        return None
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
    return str(tmp_path)


def test_set_size():
    assert len(CASES) >= 40
    assert len([r for r in _rows() if len(r[0]) < 30 and r[0].startswith("v2-")]) == 8


@pytest.mark.parametrize("idx", range(len(CASES) + len(DEV_LABELS)))
def test_route(idx, workdir):
    rows = _rows()
    if idx >= len(rows):
        pytest.skip("dev suite not present")
    name, text, spec, var = rows[idx]
    r = HRR.route(text, kind=_kind(spec), workdir=workdir)
    assert r.spec == spec, f"{name!r}: got {r.spec} ({r.reason}), wanted {spec}"
    if var is not None:
        assert r.variant == var, f"{name!r}: got variant {r.variant!r}, wanted {var!r}"


def test_overall_accuracy(workdir):
    bad = _score(_rows(), _kind, workdir)
    assert not bad, bad


def test_variant_addons_exist():
    for v in ("code.build.algorithm", "code.build.feature"):
        r = HRR.route("x", kind="code")
        r.spec, r.variant = "code.build", v.split(".")[2]
        r.family = "code"
        assert "[Harness:" in addon(r)
    assert signals.variant("build", "add a --json flag") == "cli"


def test_trace_note_is_exception_safe():
    import threading
    from meshpool_harness.agent.core import Agent
    a = Agent.__new__(Agent)
    a.trace, a._trace_lock = [], threading.Lock()
    a._trace_note("review", "LGTM")
    a._trace_note("mutation", "0 of 5 survived")
    assert a.trace == [{"tool": "review", "s": 0, "ok": True, "arg": "LGTM"},
                       {"tool": "mutation", "s": 0, "ok": True, "arg": "0 of 5 survived"}]
    Agent.__new__(Agent)._trace_note("review", "x")      # no trace attribute: must not raise
