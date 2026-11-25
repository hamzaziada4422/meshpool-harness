"""Vanilla Pi 1.5.2 (the version the Meshpool app ships) on the same task, for a like-for-like baseline of mp-on-pi.
    /root/.venvs/mp/bin/python testkit/bench3/profile/run_pi152.py"""
import json, os, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[2]), str(HERE.parents[1] / "loop")]
import arms as A
import run as R
from capture import CaptureServer
import importlib
task = list(importlib.import_module("testkit.bench3.tasks").get("xlsx_edit"))[0]
cli = Path.home() / ".cache/meshpool-small/bench3/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"
out = HERE / "captures"
srv = CaptureServer(out).start()
(out / "pi152.jsonl").unlink(missing_ok=True)
root = Path(tempfile.mkdtemp(prefix="prof-pi152-"))
wd = root / "work"
R.prepare_workdir(task, wd)
pd = root / "home" / "pi"
pd.mkdir(parents=True)
model = "deepseek-v4.1-flash"
models = {"providers": {"meter": {"baseUrl": srv.base_for("pi152"), "api": "openai-completions", "apiKey": "dummy",
          "models": [{"id": model, "name": model, "reasoning": False, "input": ["text"], "contextWindow": 400000, "maxTokens": 64000,
                      "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}]}}}
(pd / "models.json").write_text(json.dumps(models))
r = A.run_proc(["node", str(cli), "--provider", "meter", "--model", model, "-p", task.prompt], cwd=wd,
               env=A._env({"PI_CODING_AGENT_DIR": str(pd), "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}), timeout=120,
               log=out / "logs" / "pi152.log")
print(r, srv.seq)
