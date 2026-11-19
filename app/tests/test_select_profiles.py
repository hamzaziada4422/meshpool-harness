"""testkit/loop/select_profiles.py on a small synthetic results file: lexicographic choice, noise guard, min-tasks."""
import json
import sys
from pathlib import Path

LOOP = Path(__file__).resolve().parents[2] / "testkit" / "loop"
sys.path.insert(0, str(LOOP))

import select_profiles as SP  # noqa: E402


def row(task, arm, ok, usd, secs, keys, suite="debug"):
    return {"task": task, "suite": suite, "arm": arm, "pass": ok, "usd": usd, "secs": secs, "route_keys": keys}


def write(tmp_path, rows):
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return str(p)


def make(tasks, keys, per_profile):
    """per_profile: {profile: [(ok, usd, secs) per task]}"""
    out = []
    for prof, vals in per_profile.items():
        for t, (ok, usd, secs) in zip(tasks, vals):
            out.append(row(t, f"profile:{prof}", ok, usd, secs, keys))
    return out


def run(tmp_path, rows, *args):
    out = tmp_path / "p.json"
    SP.main([write(tmp_path, rows), "--out", str(out), "--md", str(tmp_path / "p.md"), *args])
    return json.loads(out.read_text()), (tmp_path / "p.md").read_text()


def test_lexicographic_choice_and_default(tmp_path):
    dbg = ["code.debug", "code.debug.python"]
    bld = ["code.build"]
    rows = (make(["d1", "d2", "d3", "d4"], dbg, {"full": [(1, .10, 50)] * 4, "lean": [(1, .10, 50)] * 3 + [(0, .01, 5)]}))
    for t in ("d1", "d2", "d3", "d4"):
        for r in rows:
            if r["task"] == t:
                r["route_keys"] = dbg
    rows += make(["b1", "b2", "b3"], bld, {"full": [(1, .5, 90)] * 3, "lean": [(1, .5, 90)] * 3})
    res, md = run(tmp_path, rows)
    # full solves 7, lean 6: full is the default and nothing needs its own assignment
    assert res["default"] == "full" and res["assign"] == {}
    assert "code.debug" in md and "default is the best here" in md


def test_quality_beats_cost_and_cost_beats_time():
    tasks = {"a": {"x": {"solved": 1, "usd": 9, "secs": 1}, "y": {"solved": 0, "usd": 0, "secs": 0}},
             "b": {"x": {"solved": 1, "usd": 9, "secs": 1}, "y": {"solved": 1, "usd": 0, "secs": 0}}}
    assert SP.best(tasks, ["a", "b"], ["x", "y"]) == "x"                  # more solved wins despite the cost
    t2 = {"a": {"x": {"solved": 1, "usd": 2, "secs": 1}, "y": {"solved": 1, "usd": 1, "secs": 99}}}
    assert SP.best(t2, ["a"], ["x", "y"]) == "y"                          # tie on solved: cheaper wins over faster
    t3 = {"a": {"x": {"solved": 1, "usd": 1, "secs": 9}, "y": {"solved": 1, "usd": 1, "secs": 3}}}
    assert SP.best(t3, ["a"], ["x", "y"]) == "y"                          # tie on cost: faster wins


def test_group_assignment_beats_default_on_solved(tmp_path):
    kd, kb = ["code.debug"], ["code.build"]
    rows = make(["d1", "d2", "d3"], kd, {"full": [(1, .1, 10)] * 3, "lean": [(1, .1, 10)] * 3})
    rows += make(["b1", "b2", "b3", "b4"], kb, {"full": [(1, .1, 10), (0, .1, 10), (0, .1, 10), (1, .1, 10)],
                                               "lean": [(1, .1, 10)] * 4})
    res, md = run(tmp_path, rows)
    # overall: lean 7, full 5 -> default lean; build: lean 4 vs 2 but lean is the default, so it inherits
    assert res["default"] == "lean" and res["assign"] == {}
    res, _ = run(tmp_path, rows, "--default", "full")
    assert res["assign"] == {"code.build": "lean"}                      # lean solves more on build than full
    assert "solves 4 vs 2" in _read_md(tmp_path)


def _read_md(tmp_path):
    return (tmp_path / "p.md").read_text()


def test_noise_guard_needs_15_percent_saving(tmp_path):
    k = ["code.test"]
    same = {"full": [(1, 1.00, 10)] * 3 + [(1, 1.00, 10)] * 3, "lean": [(1, .90, 10)] * 6}       # 10% cheaper
    res, md = run(tmp_path, make([f"t{i}" for i in range(6)], k, same), "--default", "full")
    assert res["assign"] == {} and "inside the noise" in md
    cheap = {"full": [(1, 1.00, 10)] * 6, "lean": [(1, .80, 10)] * 6}                            # 20% cheaper
    res, md = run(tmp_path, make([f"t{i}" for i in range(6)], k, cheap), "--default", "full")
    assert res["assign"] == {"code.test": "lean"} and "20% less" in md


def test_min_tasks(tmp_path):
    k = ["code.build", "code.build.cli"]
    rows = make(["a", "b"], k, {"full": [(0, .1, 5)] * 2, "lean": [(1, .1, 5)] * 2})
    rows += make(["c", "d", "e"], ["code.build"], {"full": [(1, .1, 5)] * 3, "lean": [(1, .1, 5)] * 3})
    res, md = run(tmp_path, rows, "--default", "full")
    assert "code.build.cli" not in md and "code.build " in md            # 2 tasks: too few for its own group
    assert res["assign"] == {"code.build": "lean"}
    res, md = run(tmp_path, rows, "--default", "full", "--min-tasks", "2")
    assert res["assign"]["code.build.cli"] == "lean"


def test_only_common_tasks_and_arm_mapping_and_eval_guard(tmp_path):
    k = ["code.debug"]
    rows = make(["a", "b", "c"], k, {"full": [(1, .1, 5)] * 3, "lean": [(1, .1, 5)] * 3})
    rows.append(row("z", "profile:lean", 1, 0, 1, k))                    # only lean ran z: not compared
    rows.append(row("e1", "profile:lean", 1, 0, 1, k, suite="v2_eval"))  # held-out rows are dropped by default
    rows += [row(t, "mine", 1, .05, 5, k) for t in ("a", "b", "c")]
    tasks, keys, skipped = SP.collect(rows, {"mine": "lean+review"})
    assert skipped == 1 and "e1" not in tasks and "lean+review" in tasks["a"]
    tasks2, _, skipped2 = SP.collect(rows, {}, include_eval=True)
    assert skipped2 == 0 and "e1" in tasks2
    res = SP.select(tasks, keys)
    assert res["n_tasks"] == 3 and res["profiles"] == ["full", "lean", "lean+review"]


def test_keys_fall_back_to_route_then_suite():
    assert SP.keys_of({"route_keys": ["code.debug", "code.debug.perf"]}) == ["code.debug", "code.debug.perf"]
    assert SP.keys_of({"route": "code.build", "suite": "debug"}) == ["code.build"]
    assert SP.keys_of({"route": None, "suite": "debug"}) == ["code.debug"]
    assert SP.keys_of({"route": {"spec": "code.debug", "variant": "perf", "language": "python"}}) == [
        "code.debug", "code.debug.perf", "code.debug.python", "code.debug.perf.python"]
