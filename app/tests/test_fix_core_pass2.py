import time

from meshpool_harness.hrouter.route import route


def test_route_is_fast_on_huge_input():
    for t in ("a" * 60000, "word " * 12000, "error: foo\n  File x.py line 3\n" * 2500, " " * 60000):
        t0 = time.time()
        route(t, kind="code")
        assert time.time() - t0 < 3, len(t)
