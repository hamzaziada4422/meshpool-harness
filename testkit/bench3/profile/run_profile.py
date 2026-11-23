"""Run each bench3 arm on one office task against the fake capture upstream (no model calls).

    /root/.venvs/mp/bin/python testkit/bench3/profile/run_profile.py [--arms codex,pi,...] [--task xlsx_edit]
        [--mp-src /tmp/claude-0/base/app] [--out testkit/bench3/profile/captures]
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
B3 = HERE.parent
ROOT = B3.parents[1]
for p in (str(B3), str(ROOT), str(ROOT / "testkit" / "loop"), str(HERE)):
    sys.path.insert(0, p)
import arms as A                       # noqa: E402
from capture import CaptureServer      # noqa: E402
import run as R                        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="codex,pi,mp-cli,mp-work,mp-on-pi,mp-on-codex")
    ap.add_argument("--task", default="xlsx_edit")
    ap.add_argument("--mp-src", default="/tmp/claude-0/base/app")
    ap.add_argument("--python", default=A.DEFAULT_PYTHON)
    ap.add_argument("--out", default=str(HERE / "captures"))
    ap.add_argument("--model", default="deepseek-v4.1-flash", help="the model id of the baseline run (docs/results/v4/baseline-0.0.12-flash/run.json)")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--calls", type=int, default=1, help="scripted tool calls before the final text")
    ap.add_argument("--out-suffix", default="")
    ap.add_argument("--delay", type=float, default=0.0, help="hold each reply back this many seconds (MCP servers need a few)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    import importlib
    task = list(importlib.import_module("testkit.bench3.tasks").get(a.task))[0]
    srv = CaptureServer(out, delay=a.delay, calls=a.calls).start()
    for arm in a.arms.split(","):
        (out / f"{arm}.jsonl").unlink(missing_ok=True)
        root = Path(tempfile.mkdtemp(prefix=f"prof-{arm}-"))
        wd = root / "work"
        R.prepare_workdir(task, wd)
        res = A.run_arm(arm, prompt=task.prompt, workdir=wd, home=root / "home", base_url=srv.base_for(arm),
                        model=a.model, timeout=a.timeout, log=out / "logs" / f"{arm}.log", mp_src=a.mp_src,
                        python=a.python, task_kind=task.kind)
        n = srv.seq.get(arm, 0)
        print(f"{arm}: requests={n} rc={res.get('rc')} secs={res.get('secs')} err={res.get('error')[:150]!r}", flush=True)
    srv.close()


if __name__ == "__main__":
    main()
