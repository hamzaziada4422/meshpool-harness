"""Show why a task's reference fails its hidden tests: python3 v2/why.py TASK_ID"""
import importlib, glob, os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common
for f in sorted(glob.glob(str(HERE / "t_*.py"))):
    importlib.import_module(Path(f).stem)
for tid in sys.argv[1:]:
    t = next(x for x in common.TASKS if x["id"] == tid)
    d = tempfile.mkdtemp()
    for rel, b in {**t["files"], **t["ref"], **t["grade"]["hidden"]}.items():
        if rel == "__answer__":
            continue
        p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(b)
    r = subprocess.run(t["grade"]["check"], shell=True, cwd=d, capture_output=True, text=True)
    print(f"== {tid}\n{(r.stdout + r.stderr)[-700:]}")
