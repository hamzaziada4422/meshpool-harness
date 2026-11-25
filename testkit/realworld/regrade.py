"""Re-run the (current) graders of the given tasks on a run's kept workspaces and append corrected rows.

    python testkit/realworld/regrade.py <run dir> pdf_facts[,task...]

Used when a grader bug is fixed after runs finished (the workspace is kept for non-swe tasks); the corrected row
replaces the old one in report.py (last row wins) and records the old verdict in "regraded_from".
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from testkit.realworld import tasks as T  # noqa: E402

run, ids = Path(sys.argv[1]), sys.argv[2]
by = {t.id: t for t in T.get(ids)}
rows = {}
for line in (run / "results.jsonl").read_text().splitlines():
    r = json.loads(line)
    rows[(r["task"], r["arm"], r["rep"])] = r
with open(run / "results.jsonl", "a") as f:
    for (tid, arm, rep), r in sorted(rows.items()):
        if tid not in by:
            continue
        g = by[tid].grade(run / "work" / tid / arm / str(rep), r.get("reply_tail", ""))
        if bool(g["ok"]) != r["ok"] or g["detail"][:500] != r["detail"]:
            new = dict(r, ok=bool(g["ok"]), detail=str(g["detail"])[:500], regraded_from=[r["ok"], r["detail"][:200]])
            f.write(json.dumps(new) + "\n")
            print(tid, arm, rep, r["ok"], "->", new["ok"], new["detail"][:80])
