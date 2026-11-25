"""Unit tests for testkit/gradekit.py with real captured tool outputs."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from testkit import gradekit as gk  # noqa: E402

CARGO_THREE = """\
     Running unittests src/lib.rs (target/debug/deps/react-92944fbb5f760a8b)

running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

     Running tests/react.rs (target/debug/deps/react-f6b35e30d563156b)

running 22 tests
test result: ok. 22 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s

   Doc-tests react

running 2 tests
test result: ok. 0 passed; 0 failed; 2 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

CARGO_REAL = """\
test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
test result: ok. 20 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s
test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.32s
"""

CARGO_FAIL = """\
test result: FAILED. 18 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s
"""

PYTEST_FULL = """\
ERROR p_test.py::test_c - RuntimeError
ERROR p_test.py::test_d - RuntimeError
=============== 1 failed, 1 passed, 1 skipped, 2 errors in 0.02s ===============
"""

UNITTEST_BAD = """\
----------------------------------------------------------------------
Ran 5 tests in 0.001s

FAILED (failures=1, errors=1, skipped=1)
"""

NODE = """\
1..4
# tests 4
# suites 0
# pass 3
# fail 1
# cancelled 0
# skipped 0
# todo 0
# duration_ms 57.305671
"""


def test_cargo_counts_every_result_line():
    c = gk.count_tests(CARGO_THREE)
    assert c["passed"] == 22 and c["failed"] == 0 and c["skipped"] == 2 and c["total"] == 24
    c = gk.count_tests(CARGO_REAL)
    assert (c["passed"], c["failed"]) == (22, 0)  # 0 + 20 + 2, not just the doc-test line


def test_cargo_doc_test_line_alone_is_not_the_answer():
    last = CARGO_THREE.strip().splitlines()[-1]
    assert gk.count_tests(last)["passed"] == 0
    assert gk.count_tests(CARGO_THREE)["passed"] == 22


def test_cargo_failures_counted():
    c = gk.count_tests(CARGO_FAIL + CARGO_REAL)
    assert (c["passed"], c["failed"]) == (40, 2)


@pytest.mark.parametrize("out,exp", [
    ("== 3 passed, 1 failed, 2 errors in 0.1s ==", (3, 1, 2, 0)),
    ("5 passed in 0.02s", (5, 0, 0, 0)),
    ("1 failed, 4 passed in 1.50s", (4, 1, 0, 0)),
    (PYTEST_FULL, (1, 1, 2, 1)),
    ("========== 12 passed, 2 skipped, 3 warnings in 0.31s ==========", (12, 0, 0, 2)),
    ("1 error in 0.05s", (0, 0, 1, 0)),
])
def test_pytest_summary(out, exp):
    c = gk.count_tests(out)
    assert (c["passed"], c["failed"], c["errors"], c["skipped"]) == exp


def test_node_and_unittest():
    c = gk.count_tests(NODE)
    assert (c["passed"], c["failed"]) == (3, 1)
    c = gk.count_tests(UNITTEST_BAD)
    assert (c["passed"], c["failed"], c["errors"], c["skipped"]) == (2, 1, 1, 1)
    c = gk.count_tests("Ran 7 tests in 0.002s\n\nOK\n")
    assert (c["passed"], c["total"]) == (7, 7)
    c = gk.count_tests("Ran 7 tests in 0.002s\n\nOK (skipped=2)\n")
    assert (c["passed"], c["skipped"]) == (5, 2)


def test_count_tests_empty_and_junk():
    assert gk.count_tests("error[E0432]: unresolved import")["total"] == 0


def test_numbers_and_amounts():
    assert gk.text_has_amount("Total: $4,308.96 USD", 4308.96)
    assert gk.text_has_amount("total 4308.96", 4308.96)
    assert gk.text_has_amount("about 4308.959", 4308.96)
    assert not gk.text_has_amount("total 4308.00", 4308.96)


def test_scratch_copy_and_digest(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("y")
    os.symlink("a.txt", tmp_path / "link")
    before = gk.tree_digest(tmp_path)
    with gk.scratch_copy(tmp_path) as w:
        assert (w / "sub" / "b.txt").read_text() == "y"
        assert (w / "link").is_symlink()
        (w / "new.txt").write_text("z")
        (w / "a.txt").write_text("changed")
        kept = w
    assert not kept.exists()
    assert gk.tree_digest(tmp_path) == before
    (tmp_path / "c.txt").write_text("1")
    assert gk.tree_digest(tmp_path) != before


def test_run_helper():
    rc, out = gk.run(["echo", "hi"])
    assert rc == 0 and "hi" in out
    rc, out = gk.run(["sleep", "5"], timeout=1)
    assert rc == -9 and "timeout" in out
    assert gk.run(["definitely-not-a-command-xyz"])[0] == -1


def test_safe_never_raises():
    @gk.safe
    def boom(*a):
        raise ValueError("bad")
    r = boom(1)
    assert r["ok"] is False and "bad" in r["detail"]


def test_recalc_xlsx_outside_workspace(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["A2"], ws["A3"] = 2, 3, "=A1*A2"
    p = tmp_path / "t.xlsx"
    wb.save(p)
    before = gk.tree_digest(tmp_path)
    rp = gk.recalc_xlsx(p)
    assert tmp_path not in rp.parents
    assert openpyxl.load_workbook(rp, data_only=True).active["A3"].value == 6
    assert gk.tree_digest(tmp_path) == before
