"""Harness optimizer: propose, probe, compose (after Google Research's Harness², Apache-2.0), with our ground truth.

    python testkit/loop/optimize.py --key code.debug.perf --model deepseek-v4.1-flash [--propose 3] [--repeats 2]

1. Tasks: every loop task whose route resolves to KEY (the spec, spec.variant, or spec.variant.language).
2. Propose: candidate edits to that one harness. A fixed catalogue (drop each optional tool; keep only the first
   sentence of the method; ask for a one-line reply), plus, with --propose N, N rewrites of the method from a cheap
   model that sees the method and the measured failures, calls and output tokens.
3. Probe: every candidate races the current harness on those tasks, the same hour and model, with repeats (bench.py,
   one arm per candidate pointed at its own specs file).
4. Compose: a candidate is adopted only if quality is not worse beyond chance AND it is cheaper or faster beyond
   chance (or better on quality). Adopted edits are merged into app/defaults/specs.json; each decision and its
   evidence goes to testkit/loop/lessons.jsonl, so the next round starts from what worked.

Budget: --budget caps the run (bench.py's billed-spend guard).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1] / "app"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(APP))

import bench  # noqa: E402
from meshpool_harness.hrouter import route as R  # noqa: E402
from meshpool_harness.hrouter.route import _keys, addon, dropped  # noqa: E402
from meshpool_harness.hrouter.specs import get as spec_of, overrides  # noqa: E402

SPECS = APP / "defaults" / "specs.json"
LESSONS = HERE / "lessons.jsonl"


def tasks_for(key: str, suites: list[str]) -> list[dict]:
    out = []
    for t in bench.load_suites(suites):
        wd = bench.setup_workdir(t)
        r = R(t["prompt"], kind="code" if t.get("mode", "code") == "code" else "cowork", workdir=wd)
        if key in _keys(r):
            out.append(t)
    return out


def catalogue(key: str) -> list[tuple[str, dict]]:
    base = spec_of(".".join(key.split(".")[:2]) if key.startswith("code.") else key.split(".")[0])
    method = overrides().get(key, {}).get("addon", base.addon)
    cands = []
    for tool in sorted(set(base.tools) | {"bash_many", "fetch_url", "use_skill", "glob"}):
        cands.append((f"drop {tool}", {"drop": sorted(set(base.drop) | {tool})}))
    if method and ". " in method:
        cands.append(("method: first sentence only", {"addon": method.split(". ")[0] + "."}))
    cands.append(("one-line final reply", {"addon_extra": "Finish with one line: what you changed and the command "
                                                          "that shows it works."}))
    return cands


def proposals(key: str, n: int, evidence: str, model: str) -> list[tuple[str, dict]]:
    """N rewrites of the method from a cheap model; empty on any failure (the catalogue still runs)."""
    if n <= 0:
        return []
    left = bench.cap_left(model)
    if left is not None and left < bench.MARGIN_USD:
        print("proposer skipped: the spending cap is reached", flush=True)
        return []
    import urllib.request
    base = (os.environ.get("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")
    cur = overrides().get(key, {}).get("addon") or spec_of(
        ".".join(key.split(".")[:2]) if key.startswith("code.") else key.split(".")[0]).addon
    prompt = (f"You tune a coding agent's instructions for one kind of task ({key}). Current instructions:\n{cur}\n\n"
              f"Measured on real tasks:\n{evidence}\n\nWrite {n} alternative instruction texts that would make the agent "
              "solve these tasks in fewer model calls and fewer output tokens without lowering the solve rate. Each "
              "under 90 words. Reply as JSON: {\"variants\": [\"...\", ...]}")
    body = json.dumps({"model": model, "max_tokens": 900, "messages": [{"role": "user", "content": prompt}],
                       "response_format": {"type": "json_object"}}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, headers={
        "Authorization": f"Bearer {os.environ.get('MESHPOOL_API_KEY', '')}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = json.loads(r.read())["choices"][0]["message"]["content"]
        vs = json.loads(txt[txt.find("{"): txt.rfind("}") + 1]).get("variants") or []
        return [(f"proposed rewrite {i + 1}", {"addon": str(v)}) for i, v in enumerate(vs[:n]) if str(v).strip()]
    except Exception as e:  # noqa: BLE001
        print(f"proposer failed: {e}", flush=True)
        return []


def evidence_of(out: Path, arm: str = "routed") -> str:
    p = out / "results.jsonl"
    if not p.exists():
        return "no runs yet"
    rows = [json.loads(l) for l in p.read_text().splitlines() if json.loads(l).get("arm") == arm]
    lines = [f"{r['task']}: {'solved' if r['pass'] else 'FAILED (' + r.get('why', '')[:80] + ')'}, "
             f"{r.get('model_calls', 0)} calls, {r.get('completion_tokens', 0)} output tokens" for r in rows[:20]]
    return "\n".join(lines) or "no runs yet"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True, help="spec key, e.g. code.debug, code.debug.perf, code.build.cli.python")
    ap.add_argument("--suites", default="debug,build,hard,deep")
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--propose", type=int, default=0)
    ap.add_argument("--max-candidates", type=int, default=4)
    ap.add_argument("--budget", default="1.00")
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true", help="list tasks and candidates, spend nothing")
    a = ap.parse_args()
    bench.load_key_file()
    tasks = tasks_for(a.key, a.suites.split(","))
    out = Path(a.out or tempfile.mkdtemp(prefix=f"opt-{a.key}-"))
    cands = catalogue(a.key)
    if a.propose:
        cands += proposals(a.key, a.propose, evidence_of(out), a.model)
    cands = cands[: a.max_candidates + a.propose]
    print(f"{a.key}: {len(tasks)} tasks ({', '.join(t['id'] for t in tasks)}); {len(cands)} candidates:", flush=True)
    for name, edit in cands:
        print(f"  - {name}: {json.dumps(edit)[:120]}")
    if a.dry_run or not tasks:
        return
    arms, envs = ["routed"], []
    cur = json.loads(SPECS.read_text()) if SPECS.exists() else {"specs": {}}
    for i, (name, edit) in enumerate(cands):
        spec = json.loads(json.dumps(cur))
        spec.setdefault("specs", {}).setdefault(a.key, {}).update(edit)
        f = out / f"cand{i}.json"
        f.write_text(json.dumps(spec))
        arms.append(f"cand{i}")
        envs += ["--arm-env", f"cand{i}:MH_SPECS_FILE={f}", "--arm-env", f"cand{i}:MH_HROUTER=1"]
    cmd = [sys.executable, str(HERE / "bench.py"), "--arms", ",".join(arms), "--suites", a.suites,
           "--tasks", ",".join(t["id"] for t in tasks), "--repeats", str(a.repeats), "--models", a.model,
           "--budget", a.budget, "--out", str(out), "--no-spend", *envs]
    subprocess.run(cmd)
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines()]
    for r in rows:
        r.setdefault("rep", 0)
    by = {(r["task"], r["arm"], r["rep"]): r for r in rows}
    keys = sorted({(r["task"], r["rep"]) for r in rows})
    adopted = []
    for i, (name, edit) in enumerate(cands):
        c = bench.compare(by, keys, f"cand{i}", "routed")
        win = c["quality"] == "better" or (c["quality"] != "worse" and (c["cheaper"] == "cheaper" or c["faster"] == "faster"))
        lesson = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "key": a.key, "candidate": name,
                  "edit": edit, "model": a.model, "n": c["n"], "quality": c["quality"], "p": round(c["p"], 3),
                  "cost": [round(x, 3) for x in c["cost"]], "secs": [round(x, 3) for x in c["secs"]], "adopted": win}
        with LESSONS.open("a") as f:
            f.write(json.dumps(lesson) + "\n")
        print(f"{'ADOPT' if win else 'reject'} {name}: quality {c['quality']} (p={c['p']:.2f}), cost {c['cheaper']} "
              f"{c['cost'][0]:.2f}x, time {c['faster']} {c['secs'][0]:.2f}x", flush=True)
        if win:
            adopted.append((c["cost"][0], name, edit))
    if adopted:                                    # compose: the single best edit (edits are not assumed to stack)
        _, name, edit = min(adopted)
        cur.setdefault("specs", {}).setdefault(a.key, {}).update(edit)
        SPECS.write_text(json.dumps(cur, indent=2) + "\n")
        print(f"composed into {SPECS}: {name}")


if __name__ == "__main__":
    main()
