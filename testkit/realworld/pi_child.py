"""The Pi arm, exactly as testkit/loop/bench.py runs it (run_pi_agent, unrouted): Pi through Meshpool's own Pi
integration (harnesses/pi_rpc.PiAgent), auto mode, on the endpoint in $MESHPOOL_HOME/meshpool.config.

    PYTHONPATH=<meshpool app> MESHPOOL_HOME=<private home> python pi_child.py <prompt-file> <workdir>

Prints every agent event as one JSON line (the trace), then a final {"type": "result", ...} line.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    prompt = open(sys.argv[1], encoding="utf-8").read()
    wd = sys.argv[2]
    from meshpool_harness import config as _config
    _config.ensure_home()
    _config.load_into_env()
    from meshpool_harness.harnesses import PI
    from meshpool_harness.harnesses.pi_rpc import PiAgent

    def emit(ev):
        if isinstance(ev, dict) and ev.get("type") not in ("token", "delta", "thinking"):
            sys.stdout.write(json.dumps(ev, ensure_ascii=False, default=str)[:20000] + "\n")
            sys.stdout.flush()
        events.append(ev)
    events: list = []
    a = PiAgent(dict(PI), wd, mode="auto", emit=emit)
    if os.environ.get("MH_MODEL"):
        a.model = os.environ["MH_MODEL"]
    answer, err = "", None
    try:
        answer = a.send(prompt)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        try:
            a.close()
        except Exception:  # noqa: BLE001
            pass
    errors = [e.get("text", "") for e in events if isinstance(e, dict) and e.get("type") == "error"]
    print(json.dumps({"type": "result", "result": answer or "", "error": err or (errors[-1][:500] if errors else None),
                      "usage": dict(getattr(a, "usage", {}) or {})}, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
