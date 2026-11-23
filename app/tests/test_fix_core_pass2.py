import time

from meshpool_harness.hrouter.route import route


def test_route_is_fast_on_huge_input():
    for t in ("a" * 60000, "word " * 12000, "error: foo\n  File x.py line 3\n" * 2500, " " * 60000):
        t0 = time.time()
        route(t, kind="code")
        assert time.time() - t0 < 3, len(t)


def _bounded(fn, *a, **k):
    t0 = time.time()
    fn(*a, **k)
    assert time.time() - t0 < 3


def test_public_functions_bounded_on_single_token(tmp_path):
    from meshpool_harness.hrouter import classify as CL
    from meshpool_harness.hrouter import packs as P
    from meshpool_harness.hrouter import signals as S
    for n in (50_000, 3_000_000):
        for t in ("a" * n, "x/" * (n // 2), "a." * (n // 2) + "csv", "`" + "a" * n, "write " + "a. " * (n // 3)):
            for fn in (S.languages_in, S.pasted_error, S.code_sub, S.families, S.snippet_request, S.size,
                       S._algorithm):
                _bounded(fn, t)
            _bounded(S.variant, "build", t)
            _bounded(S.variant, "debug", t)
            _bounded(P.repro_commands, t, tmp_path)
            _bounded(CL.stage1, t)
            _bounded(route, t, kind="code")
