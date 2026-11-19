"""Terminal screenshots of the MeshPool CLI (CLI v2): the startup screen and a working session, drawn from scripted
events (no model calls) into the testkit terminal emulator, then to HTML, then to PNG with Playwright.

  python testkit/cli_v2_shots.py OUT_DIR       writes OUT_DIR/cli-v2-startup.png and cli-v2-session.png
  (the folder is a made-up ~/projects/demo, so no real path or spend figure ends up in an image)

The scenes are also used by app/tests/test_tui_v2.py.
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

from testkit import vt  # noqa: E402

from meshpool_harness.agent import tui as T  # noqa: E402
from meshpool_harness.agent.tui_input import FakeKeys  # noqa: E402

COLS = 100


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def make_terminal(width: int = COLS, depth: int = 24, **kw) -> tuple[T.Terminal, FakeTTY, list]:
    out = FakeTTY()
    clk = [0.0]
    t = T.Terminal(out=out, width=width, depth=depth, interactive=True, clock=lambda: clk[0], **kw)
    t.version, t.model, t.provider = "0.0.7", "deepseek-v4.1-flash", "MeshPool API"
    t.cwd = t.cwd_label = "~/projects/demo"
    t.branch = "main"
    t.mode = "auto"
    t.agents = 0
    return t, out, clk


def screen(out: io.StringIO, cols: int = COLS) -> vt.Screen:
    return vt.render(out.getvalue(), cols)


def idle(t: T.Terminal, out: io.StringIO, *keys, cols: int = COLS) -> vt.Screen:
    """Show the input box after `keys` were typed and return the screen at that moment."""
    shot = []
    t.keys_factory = lambda: FakeKeys(*keys, lambda: shot.append(screen(out, cols)))
    t.read_input()
    return shot[0]


def startup_scene(width: int = COLS, depth: int = 24, notices: bool = True):
    t, out, _ = make_terminal(width, depth)
    t.notices = ["MeshPool v0.0.8 is out with faster searches. Switch models with /model.",
                 "Connect Notion, Gmail or Slack with /connect."] if notices else []
    t.status_fn = lambda: [("err", "Not logged in"), ("err", "Run /login"), ("ok", "✔ Update installed"),
                           ("ok", "Restart to update")]
    t.agents = 2
    t.startup()
    return t, out, idle(t, out, cols=width)


PYTEST_OUT = """============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-8.3.2
rootdir: /home/dev/projects/demo
configfile: pytest.ini
plugins: cov-5.0.0
collected 15 items

tests/test_cli.py ........                                               [ 57%]
tests/test_config.py ......                                              [ 93%]
tests/test_log.py .                                                      [100%]

============================== 15 passed in 2.31s ==============================
"""

ANSWER = """Added `--verbose` to `demo/cli.py` and wired it into the logger. All 15 tests pass.

**What changed.** The flag sets the log level to `DEBUG` and prints each step as it runs:
- **`demo/cli.py`**: new `--verbose` / `-v` option; the parser passes it to `configure_logging()`.
- **`demo/log.py`**: `configure_logging(verbose)` picks the level and a longer format when verbose is on.
- **`tests/test_cli.py`**: two tests, one for each level.

**Why this way.** The flag is read once at start-up, so nothing else needs to know about it. Without it the output is exactly as before → no change for scripts.

Run it with `python -m demo --verbose run`.
"""


def session_scene(width: int = COLS, depth: int = 24):
    t, out, clk = make_terminal(width, depth)
    t.notices = ["Describe a task, or type /help for the commands. Switch models with /model."]
    t.status_fn = lambda: []
    t.startup()
    t.mode = "auto"
    t.windows["deepseek-v4.1-flash"] = 128_000
    t.echo_user("Add a --verbose flag to the CLI and run the tests")
    t.started = True
    t.turn_begin()
    ev = t.emit
    clk[0] += 0.4
    ev({"type": "model_start"})
    for chunk in ("I'll start with how the CLI parses its arguments, ", "then add the flag and check it with the tests.\n"):
        ev({"type": "text_delta", "text": chunk})
    ev({"type": "assistant", "text": "", "streamed": True})
    ev({"type": "usage", "in": 21400, "cached": 0, "out": 160, "calls": 1})
    ev({"type": "tool_start", "id": "1", "name": "read_file", "summary": "demo/cli.py"})
    clk[0] += 0.1
    ev({"type": "tool_end", "id": "1", "name": "read_file", "ok": True, "output": "\n".join(["x"] * 84)})
    ev({"type": "tool_start", "id": "2", "name": "grep", "summary": "configure_logging in demo (*.py)"})
    clk[0] += 0.2
    ev({"type": "tool_end", "id": "2", "name": "grep", "ok": True,
        "output": "demo/log.py:12: def configure_logging(level):\ndemo/cli.py:31:    configure_logging(args.level)"})
    ev({"type": "tool_start", "id": "3", "name": "edit", "summary": "demo/cli.py",
        "diff": "--- a/demo/cli.py\n+++ b/demo/cli.py\n@@\n-    configure_logging(args.level)\n"
                "+    configure_logging(args.level, verbose=args.verbose)\n+    # --verbose: DEBUG and step output\n"
                "+    parser.add_argument('-v', '--verbose', action='store_true')\n+\n+\n+\n"})
    clk[0] += 0.05
    ev({"type": "tool_end", "id": "3", "name": "edit", "ok": True, "output": "Edited demo/cli.py"})
    ev({"type": "tool_start", "id": "4", "name": "bash", "summary": "python -m pytest -q tests/", "timeout_s": 60})
    clk[0] += 2.6
    ev({"type": "tool_end", "id": "4", "name": "bash", "ok": True, "output": PYTEST_OUT})
    ev({"type": "usage", "in": 23800, "cached": 21300, "out": 410, "calls": 1})
    ev({"type": "model_start"})
    for line in ANSWER.splitlines(keepends=True):
        ev({"type": "text_delta", "text": line})
    ev({"type": "assistant", "text": "", "streamed": True})
    for u in ({"in": 98000, "cached": 91000, "out": 900, "calls": 1}, {"in": 105600, "cached": 99200, "out": 1100,
                                                                      "calls": 1}):
        ev({"type": "usage", **u})
    ev({"type": "turn_end", "turn": {}, "session": {}})
    t.effort = "medium"
    t.agents = 0
    return t, out, idle(t, out)


def render_pngs(out_dir: Path, scenes: dict) -> None:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, scr in scenes.items():
        (out_dir / f"{name}.html").write_text(scr.html(title=name))
    js = ("const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();"
          "for(const n of process.argv.slice(3)){const p=await b.newPage({deviceScaleFactor:2,viewport:{width:"
          "parseInt(process.argv[2]),height:400}});await p.goto('file://'+process.argv[1]+'/'+n+'.html');"
          "const el=await p.$('pre');const box=await el.boundingBox();"
          "await p.setViewportSize({width:Math.ceil(box.x+box.width),height:Math.ceil(box.y+box.height)});"
          "await p.screenshot({path:process.argv[1]+'/'+n+'.png'});await p.close();}await b.close();})();")
    # the page width follows the text: 100 columns of the 15px font is about 900 px
    subprocess.run(["node", "-e", js, str(out_dir), "1000", *scenes], check=True, cwd=str(ROOT))
    for name in scenes:
        (out_dir / f"{name}.html").unlink()


if __name__ == "__main__":
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "screenshots")
    scenes = {"cli-v2-startup": startup_scene()[2], "cli-v2-session": session_scene()[2]}
    render_pngs(dest, scenes)
    print("wrote", ", ".join(f"{dest / n}.png" for n in scenes))
